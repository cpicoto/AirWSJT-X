use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    let manifest_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR is set"));
    let target = std::env::var("TARGET").expect("TARGET is set");
    let repo_root = manifest_dir.parent().expect("project root should exist");
    let binaries_dir = manifest_dir.join("binaries");
    let binary_name = if target.contains("windows") {
        format!("airwsjtx_bridge-{target}.exe")
    } else {
        format!("airwsjtx_bridge-{target}")
    };
    let expected_binary = binaries_dir.join(&binary_name);

    fs::create_dir_all(&binaries_dir).expect("cannot create binaries directory");

    if (target.contains("linux") || target.contains("apple")) && !expected_binary.exists() {
        let python_script = repo_root.join("sidecar").join("airwsjtx_bridge.py");
        let wrapper = format!(
            "#!/bin/sh\nexec python3 \"{}\" \"$@\"\n",
            python_script.display()
        );
        fs::write(&expected_binary, wrapper).expect("cannot create Unix sidecar wrapper");

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut permissions = fs::metadata(&expected_binary)
                .expect("wrapper metadata missing")
                .permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(&expected_binary, permissions).expect("cannot mark wrapper executable");
        }
    } else if target.contains("windows") && !expected_binary.exists() {
        let python_script = repo_root.join("sidecar").join("airwsjtx_bridge.py");
        let escaped_script = python_script.to_string_lossy().replace('\\', "\\\\");
        let out_dir = PathBuf::from(std::env::var("OUT_DIR").expect("OUT_DIR is set"));
        let wrapper_src = out_dir.join("airwsjtx_bridge_wrapper.rs");
        let rust_code = format!(
            r#"use std::env;
use std::path::PathBuf;
use std::process::{{Command, Stdio}};

fn main() {{
    let current = env::current_exe().unwrap_or_else(|_| PathBuf::from("."));
    let mut dir = current.parent();
    let mut script = None;
    while let Some(d) = dir {{
        let candidate = d.join("sidecar").join("airwsjtx_bridge.py");
        if candidate.exists() {{
            script = Some(candidate);
            break;
        }}
        dir = d.parent();
    }}
    let script = script.unwrap_or_else(|| PathBuf::from(r"{escaped_script}"));

    let candidates = ["python", "python3", "py"];
    let mut chosen = "python";
    let mut extra: Option<&str> = None;
    for &cand in &candidates {{
        if let Ok(out) = Command::new(cand).arg("--version").output() {{
            if out.status.success() {{
                chosen = cand;
                if cand == "py" {{
                    extra = Some("-3");
                }}
                break;
            }}
        }}
    }}

    let mut cmd = Command::new(chosen);
    if let Some(arg) = extra {{
        cmd.arg(arg);
    }}
    cmd.arg(&script);
    cmd.args(env::args().skip(1));
    cmd.stdin(Stdio::inherit());
    cmd.stdout(Stdio::inherit());
    cmd.stderr(Stdio::inherit());

    match cmd.spawn() {{
        Ok(mut child) => {{
            let status = child.wait().expect("Failed to wait for python sidecar");
            std::process::exit(status.code().unwrap_or(1));
        }}
        Err(e) => {{
            eprintln!("Failed to spawn python sidecar: {{}}", e);
            std::process::exit(1);
        }}
    }}
}}
"#
        );
        fs::write(&wrapper_src, rust_code).expect("cannot write Windows sidecar wrapper source");
        let rustc = std::env::var("RUSTC").unwrap_or_else(|_| "rustc".to_string());
        let status = Command::new(rustc)
            .arg(&wrapper_src)
            .arg("-o")
            .arg(&expected_binary)
            .status()
            .expect("failed to execute rustc to compile sidecar wrapper");
        assert!(status.success(), "failed to compile Windows sidecar wrapper");
    }

    println!("cargo:rerun-if-changed={}", repo_root.join("sidecar").join("airwsjtx_bridge.py").display());

    if target.contains("windows") {
        // If an existing instance of airwsjtx or airwsjtx_bridge is still running,
        // terminate it so Windows file-locking doesn't cause tauri-build or the linker
        // to panic with OS error 5 (Access is denied).
        if let Ok(output) = Command::new("taskkill")
            .args(["/F", "/IM", "airwsjtx.exe", "/IM", "airwsjtx_bridge.exe"])
            .output()
        {
            if output.status.success() {
                std::thread::sleep(std::time::Duration::from_millis(200));
            }
        }
    }

    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("android") {
        build_android_rtlsdr(repo_root);
    }

    tauri_build::build();
}

/// Compiles the receive-only RTL-SDR stack into the Android app: pinned libusb, librtlsdr
/// patched to open an Android USB file descriptor, and the app's dump1090-fa demodulator.
/// Sources are staged by `python scripts/install_rtlsdr.py --android`.
fn build_android_rtlsdr(repo_root: &std::path::Path) {
    let staged = repo_root.join("sidecar").join("rtl-sdr").join("android");
    let scripts = repo_root.join("scripts");
    let libusb = staged.join("libusb");
    let rtl = staged.join("rtl-sdr");
    assert!(
        libusb.join("libusb").join("core.c").exists() && rtl.join("src").join("librtlsdr.c").exists(),
        "Android RTL-SDR sources are missing; run `python scripts/install_rtlsdr.py --android` first"
    );
    let libusb_sources = [
        "core.c", "descriptor.c", "hotplug.c", "io.c", "strerror.c", "sync.c",
        "os/events_posix.c", "os/threads_posix.c", "os/linux_usbfs.c", "os/linux_netlink.c",
    ];
    let rtl_sources = [
        "librtlsdr.c", "tuner_e4k.c", "tuner_fc0012.c", "tuner_fc0013.c", "tuner_fc2580.c", "tuner_r82xx.c",
    ];
    let demod_sources = ["adsb_receiver.c", "dump1090-fa/demod_2400.c", "dump1090-fa/icao_filter.c"];
    let mut build = cc::Build::new();
    build
        .include(libusb.join("android"))
        .include(libusb.join("libusb"))
        .include(libusb.join("libusb").join("os"))
        .include(rtl.join("include"))
        .include(&scripts)
        .define("AIRWSJTX_LIBRARY", None)
        .define("DETACH_KERNEL_DRIVER", None)
        .flag_if_supported("-std=gnu11")
        .opt_level(2)
        .warnings(false);
    for name in libusb_sources {
        build.file(libusb.join("libusb").join(name));
    }
    for name in rtl_sources {
        build.file(rtl.join("src").join(name));
    }
    for name in demod_sources {
        build.file(scripts.join(name));
    }
    build.compile("airwsjtx_rtlsdr");
    println!("cargo:rustc-link-lib=log");
    println!("cargo:rerun-if-changed={}", staged.display());
    for name in demod_sources {
        println!("cargo:rerun-if-changed={}", scripts.join(name).display());
    }
}
