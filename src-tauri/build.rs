use std::fs;
use std::path::PathBuf;

fn main() {
    let manifest_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR is set"));
    let target = std::env::var("TARGET").expect("TARGET is set");
    let repo_root = manifest_dir.parent().expect("project root should exist");
    let binaries_dir = manifest_dir.join("binaries");
    let expected_binary = binaries_dir.join(format!("airwsjtx_bridge-{target}"));

    fs::create_dir_all(&binaries_dir).expect("cannot create binaries directory");

    if target.contains("linux") && !expected_binary.exists() {
        let python_script = repo_root.join("sidecar").join("airwsjtx_bridge.py");
        let wrapper = format!(
            "#!/bin/sh\nexec python3 \"{}\" \"$@\"\n",
            python_script.display()
        );
        fs::write(&expected_binary, wrapper).expect("cannot create Linux sidecar wrapper");

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut permissions = fs::metadata(&expected_binary)
                .expect("wrapper metadata missing")
                .permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(&expected_binary, permissions).expect("cannot mark wrapper executable");
        }
    }

    println!("cargo:rerun-if-changed={}", repo_root.join("sidecar").join("airwsjtx_bridge.py").display());

    tauri_build::build();
}
