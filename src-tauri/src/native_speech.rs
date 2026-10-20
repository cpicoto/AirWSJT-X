use std::io::Write;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

struct Engine<'a> {
    program: &'a str,
    args: &'a [&'a str],
    dispatcher: bool,
}

const ENGINES: [Engine<'static>; 3] = [
    // --wait blocks the pipe reader, preventing client-scoped cancellation.
    Engine {
        program: "spd-say",
        args: &["-l", "en-us", "-r", "-10", "--pipe-mode"],
        dispatcher: true,
    },
    Engine {
        program: "espeak-ng",
        args: &["-v", "en-us", "-s", "155", "--"],
        dispatcher: false,
    },
    Engine {
        program: "espeak",
        args: &["-v", "en-us", "-s", "155", "--"],
        dispatcher: false,
    },
];

pub struct NativeSpeech {
    current: Mutex<Option<Arc<Mutex<Utterance>>>>,
}

pub struct Utterance {
    child: Child,
    program: String,
    cancelled: bool,
}

impl NativeSpeech {
    pub const fn new() -> Self {
        Self {
            current: Mutex::new(None),
        }
    }

    pub fn start(&self, text: &str) -> Result<Arc<Mutex<Utterance>>, String> {
        self.start_with_engines(text, &ENGINES)
    }

    fn start_with_engines(
        &self,
        text: &str,
        engines: &[Engine<'_>],
    ) -> Result<Arc<Mutex<Utterance>>, String> {
        let mut current = self.current.lock().map_err(|e| e.to_string())?;
        Self::stop_current(&mut current)?;
        for engine in engines {
            let program = engine.program;
            let mut command = Command::new(program);
            command.args(engine.args).stdout(Stdio::null());
            if engine.dispatcher {
                command.stdin(Stdio::piped());
            } else {
                command.arg(text).stdin(Stdio::null());
            }
            match command.spawn() {
                Ok(child) => {
                    let utterance = Arc::new(Mutex::new(Utterance {
                        child,
                        program: program.into(),
                        cancelled: false,
                    }));
                    *current = Some(utterance.clone());
                    if engine.dispatcher {
                        // One text line, never a pipe-mode SSIP command, even for
                        // input containing newlines or the "!-!" command prefix.
                        let line = format!(" {}\n", text.replace(['\r', '\n'], " "));
                        let result = utterance
                            .lock()
                            .map_err(|e| e.to_string())?
                            .child
                            .stdin
                            .as_mut()
                            .ok_or("Speech input pipe is unavailable")?
                            .write_all(line.as_bytes());
                        if let Err(e) = result {
                            let cleanup = Self::stop_current(&mut current);
                            return Err(format!(
                                "Cannot send speech to {program}: {e}{}",
                                cleanup.err().map(|e| format!("; {e}")).unwrap_or_default()
                            ));
                        }
                    }
                    return Ok(utterance);
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
                Err(e) => return Err(format!("Cannot start {program}: {e}")),
            }
        }
        Err("No speech engine found. Install speech-dispatcher or espeak-ng.".into())
    }

    pub fn wait(&self, utterance: Arc<Mutex<Utterance>>) -> Result<(), String> {
        let result = loop {
            {
                let mut speech = utterance.lock().map_err(|e| e.to_string())?;
                let status = speech
                    .child
                    .try_wait()
                    .map_err(|e| format!("Cannot check {}: {e}", speech.program))?;
                if let Some(status) = status {
                    break if speech.cancelled || status.success() {
                        Ok(())
                    } else {
                        Err(format!(
                            "{} failed ({status}). Check the speech engine and audio configuration.",
                            speech.program
                        ))
                    };
                }
            }
            std::thread::sleep(Duration::from_millis(25));
        };
        let mut current = self.current.lock().map_err(|e| e.to_string())?;
        // An older wait must never clear a replacement utterance.
        if current
            .as_ref()
            .is_some_and(|active| Arc::ptr_eq(active, &utterance))
        {
            *current = None;
        }
        result
    }

    pub fn stop(&self) -> Result<(), String> {
        let mut current = self.current.lock().map_err(|e| e.to_string())?;
        Self::stop_current(&mut current)
    }

    fn stop_current(current: &mut Option<Arc<Mutex<Utterance>>>) -> Result<(), String> {
        if let Some(active) = current.as_ref() {
            let mut speech = active.lock().map_err(|e| e.to_string())?;
            if speech
                .child
                .try_wait()
                .map_err(|e| e.to_string())?
                .is_none()
            {
                if let Some(mut input) = speech.child.stdin.take() {
                    // spd-say forwards this to its own dispatcher connection.
                    let cancel = input.write_all(b"!-!CANCEL SELF\n");
                    drop(input);
                    let deadline = Instant::now() + Duration::from_secs(2);
                    loop {
                        if let Some(status) = speech.child.try_wait().map_err(|e| e.to_string())? {
                            if cancel.is_ok() && status.success() {
                                speech.cancelled = true;
                                break;
                            }
                            return Err(format!(
                                "Cannot confirm {} cancellation ({status}): {cancel:?}",
                                speech.program
                            ));
                        }
                        if Instant::now() >= deadline {
                            speech
                                .child
                                .kill()
                                .map_err(|e| format!("Cannot stop {}: {e}", speech.program))?;
                            speech
                                .child
                                .wait()
                                .map_err(|e| format!("Cannot reap {}: {e}", speech.program))?;
                            return Err(format!(
                                "{} did not confirm cancellation; its client was terminated.",
                                speech.program
                            ));
                        }
                        std::thread::sleep(Duration::from_millis(25));
                    }
                } else if let Err(e) = speech.child.kill() {
                    // The child may have exited between try_wait and kill.
                    if speech
                        .child
                        .try_wait()
                        .map_err(|e| e.to_string())?
                        .is_none()
                    {
                        return Err(format!("Cannot stop {}: {e}", speech.program));
                    }
                } else {
                    speech.cancelled = true;
                }
                speech
                    .child
                    .wait()
                    .map_err(|e| format!("Cannot reap {}: {e}", speech.program))?;
            }
        }
        *current = None;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn start_fixture(speech: &NativeSpeech, mode: &str) -> Arc<Mutex<Utterance>> {
        start_fixture_text(speech, mode, mode)
    }

    fn fixture_name() -> String {
        format!(
            "{}::engine_fixture",
            module_path!().split_once("::").unwrap().1
        )
    }

    fn start_fixture_text(speech: &NativeSpeech, mode: &str, text: &str) -> Arc<Mutex<Utterance>> {
        let executable = std::env::current_exe().unwrap();
        let fixture = fixture_name();
        speech
            .start_with_engines(
                text,
                &[Engine {
                    program: executable.to_str().unwrap(),
                    args: &["--exact", &fixture, "--nocapture", "--", mode],
                    dispatcher: mode.starts_with("pipe"),
                }],
            )
            .unwrap()
    }

    #[test]
    fn engine_fixture() {
        let mode = std::env::args().skip_while(|arg| arg != "--").nth(1);
        match mode.as_deref() {
            Some("failure") => std::process::exit(7),
            Some("waiting") => std::thread::sleep(Duration::from_secs(30)),
            Some("pipe") | Some("pipe-failure") | Some("pipe-stalled") => {
                use std::io::BufRead;
                let mut lines = std::io::stdin().lock().lines();
                let text = lines.next().unwrap().unwrap();
                assert!(text.starts_with(' '));
                assert!(!text[1..].contains('\n'));
                assert_eq!(lines.next().unwrap().unwrap(), "!-!CANCEL SELF");
                if mode.as_deref() == Some("pipe-stalled") {
                    std::thread::sleep(Duration::from_secs(30));
                }
                if mode.as_deref() == Some("pipe-failure") {
                    std::process::exit(7);
                }
            }
            _ => {}
        }
    }

    #[test]
    fn preserves_engine_priority_voice_and_pacing() {
        assert_eq!(
            ENGINES.map(|e| e.program),
            ["spd-say", "espeak-ng", "espeak"]
        );
        assert_eq!(ENGINES[0].args, ["-l", "en-us", "-r", "-10", "--pipe-mode"]);
        assert!(ENGINES[0].dispatcher);
        for engine in &ENGINES[1..] {
            assert_eq!(engine.args, ["-v", "en-us", "-s", "155", "--"]);
            assert!(!engine.dispatcher);
        }
    }

    #[test]
    fn missing_engines_report_installation_hint() {
        let speech = NativeSpeech::new();
        let result = speech.start_with_engines(
            "test",
            &[Engine {
                program: "airwsjtx-missing-speech-engine",
                args: &[],
                dispatcher: false,
            }],
        );
        assert_eq!(
            result.err().unwrap(),
            "No speech engine found. Install speech-dispatcher or espeak-ng."
        );
    }

    #[test]
    fn skips_missing_engine_and_reaps_successful_child() {
        let speech = NativeSpeech::new();
        let executable = std::env::current_exe().unwrap();
        let fixture = fixture_name();
        let utterance = speech
            .start_with_engines(
                "success",
                &[
                    Engine {
                        program: "airwsjtx-missing-speech-engine",
                        args: &[],
                        dispatcher: false,
                    },
                    Engine {
                        program: executable.to_str().unwrap(),
                        args: &["--exact", &fixture, "--nocapture", "--", "success"],
                        dispatcher: false,
                    },
                ],
            )
            .unwrap();
        assert!(speech.wait(utterance.clone()).is_ok());
        assert!(utterance
            .lock()
            .unwrap()
            .child
            .try_wait()
            .unwrap()
            .is_some());
        assert!(speech.current.lock().unwrap().is_none());
    }

    #[test]
    fn failed_exit_is_reported() {
        let speech = NativeSpeech::new();
        let utterance = start_fixture(&speech, "failure");
        let error = speech.wait(utterance).unwrap_err();
        assert!(error.contains("failed"));
        assert!(error.contains('7'));
        assert!(speech.current.lock().unwrap().is_none());
    }

    #[test]
    fn spawn_errors_are_not_hidden_by_trying_other_engines() {
        let speech = NativeSpeech::new();
        let result = speech.start_with_engines(
            "test",
            &[Engine {
                program: file!(),
                args: &[],
                dispatcher: false,
            }],
        );
        assert!(result.err().unwrap().starts_with("Cannot start "));
    }

    #[test]
    fn cancellation_reaps_child_without_reporting_an_engine_failure() {
        let speech = NativeSpeech::new();
        let utterance = start_fixture(&speech, "waiting");
        speech.stop().unwrap();
        assert!(utterance
            .lock()
            .unwrap()
            .child
            .try_wait()
            .unwrap()
            .is_some());
        assert!(speech.wait(utterance).is_ok());
        speech.stop().unwrap();
    }

    #[test]
    fn replacement_stops_old_child_and_old_wait_preserves_new_child() {
        let speech = NativeSpeech::new();
        let first = start_fixture(&speech, "waiting");
        let second = start_fixture(&speech, "waiting");
        assert!(first.lock().unwrap().child.try_wait().unwrap().is_some());
        assert!(speech.wait(first).is_ok());
        assert!(Arc::ptr_eq(
            speech.current.lock().unwrap().as_ref().unwrap(),
            &second
        ));
        speech.stop().unwrap();
        assert!(speech.wait(second).is_ok());
    }

    #[test]
    fn concurrent_starts_do_not_leave_untracked_children() {
        let speech = NativeSpeech::new();
        let (first, second) = std::thread::scope(|scope| {
            let first = scope.spawn(|| start_fixture(&speech, "waiting"));
            let second = scope.spawn(|| start_fixture(&speech, "waiting"));
            (first.join().unwrap(), second.join().unwrap())
        });
        speech.stop().unwrap();
        for utterance in [first, second] {
            assert!(utterance
                .lock()
                .unwrap()
                .child
                .try_wait()
                .unwrap()
                .is_some());
            assert!(speech.wait(utterance).is_ok());
        }
        assert!(speech.current.lock().unwrap().is_none());
    }

    #[test]
    fn dispatcher_cancels_only_its_own_client_and_reaps_on_replacement() {
        let speech = NativeSpeech::new();
        let first = start_fixture(&speech, "pipe");
        let second = start_fixture(&speech, "pipe");
        assert!(speech.wait(first).is_ok());
        speech.stop().unwrap();
        assert!(speech.wait(second).is_ok());
    }

    #[test]
    fn dispatcher_cancellation_failure_is_reported() {
        let speech = NativeSpeech::new();
        let utterance = start_fixture(&speech, "pipe-failure");
        assert!(speech.stop().unwrap_err().contains("Cannot confirm"));
        assert!(speech.wait(utterance).unwrap_err().contains("failed"));
    }

    #[test]
    fn dispatcher_text_cannot_inject_pipe_commands() {
        let speech = NativeSpeech::new();
        let utterance = start_fixture_text(&speech, "pipe", "!-!CANCEL ALL\r\nAzimuth 10");
        speech.stop().unwrap();
        assert!(speech.wait(utterance).is_ok());
    }

    #[test]
    fn stalled_dispatcher_is_reaped_and_cancellation_timeout_is_reported() {
        let speech = NativeSpeech::new();
        let utterance = start_fixture(&speech, "pipe-stalled");
        assert!(speech
            .stop()
            .unwrap_err()
            .contains("did not confirm cancellation"));
        assert!(utterance
            .lock()
            .unwrap()
            .child
            .try_wait()
            .unwrap()
            .is_some());
        assert!(speech.wait(utterance).is_err());
        speech.stop().unwrap();
    }
}
