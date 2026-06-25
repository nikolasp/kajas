use std::{
    env,
    error::Error,
    io,
    net::{TcpStream, ToSocketAddrs},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use tauri::Manager;

type SetupResult<T> = Result<T, Box<dyn Error>>;

struct BackendProcess {
    child: Mutex<Option<Child>>,
}

impl BackendProcess {
    fn new(child: Child) -> Self {
        Self {
            child: Mutex::new(Some(child)),
        }
    }
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        if let Ok(mut child) = self.child.lock() {
            if let Some(mut child) = child.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let host = env::var("KAJAS_DESKTOP_HOST").unwrap_or_else(|_| "127.0.0.1".into());
            let port = env::var("KAJAS_DESKTOP_PORT")
                .ok()
                .and_then(|value| value.parse::<u16>().ok())
                .unwrap_or(8765);

            if !port_is_open(&host, port) {
                let backend = start_backend(&host, port)?;
                app.manage(backend);
                wait_for_backend(&host, port, Duration::from_secs(20))?;
            }

            let app_url = format!("http://{host}:{port}/");
            let allowed_origin = format!("http://{host}:{port}");
            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::External(app_url.parse().unwrap()),
            )
            .title("Kajas")
            .inner_size(1280.0, 820.0)
            .min_inner_size(960.0, 640.0)
            .on_navigation(move |url| {
                let origin = match url.port() {
                    Some(port) => format!(
                        "{}://{}:{}",
                        url.scheme(),
                        url.host_str().unwrap_or(""),
                        port
                    ),
                    None => format!("{}://{}", url.scheme(), url.host_str().unwrap_or("")),
                };
                origin == allowed_origin
            })
            .build()?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Kajas desktop");
}

fn start_backend(host: &str, port: u16) -> SetupResult<BackendProcess> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let frontend_dir = manifest_dir
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| {
            io::Error::new(io::ErrorKind::NotFound, "failed to resolve frontend dir")
        })?;
    let repo_dir = frontend_dir
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| {
            io::Error::new(io::ErrorKind::NotFound, "failed to resolve repo dir")
        })?;
    let backend_dir = repo_dir.join("backend");
    let frontend_dist = frontend_dir.join("dist");

    let mut command = backend_command();
    command
        .current_dir(&repo_dir)
        .env("PYTHONPATH", backend_dir)
        .arg("serve")
        .arg("--host")
        .arg(host)
        .arg("--port")
        .arg(port.to_string())
        .arg("--frontend-dir")
        .arg(frontend_dist)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = command.spawn()?;
    Ok(BackendProcess::new(child))
}

fn backend_command() -> Command {
    if let Ok(command) = env::var("KAJAS_BACKEND_CMD") {
        return Command::new(command);
    }

    let python = if cfg!(windows) { "python" } else { "python3" };
    let mut command = Command::new(python);
    command.arg("-m").arg("kajas.cli");
    command
}

fn wait_for_backend(host: &str, port: u16, timeout: Duration) -> SetupResult<()> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if port_is_open(host, port) {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(150));
    }

    Err(io::Error::new(
        io::ErrorKind::TimedOut,
        format!("Kajas backend did not start on {host}:{port}"),
    )
    .into())
}

fn port_is_open(host: &str, port: u16) -> bool {
    let timeout = Duration::from_millis(150);
    match (host, port).to_socket_addrs() {
        Ok(addrs) => addrs
            .into_iter()
            .any(|addr| TcpStream::connect_timeout(&addr, timeout).is_ok()),
        Err(_) => false,
    }
}
