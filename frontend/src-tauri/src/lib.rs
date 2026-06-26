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
use tauri_plugin_shell::{process::CommandChild, ShellExt};

type SetupResult<T> = Result<T, Box<dyn Error>>;

struct BackendProcess {
    child: Mutex<Option<BackendChild>>,
}

impl BackendProcess {
    fn new(child: BackendChild) -> Self {
        Self {
            child: Mutex::new(Some(child)),
        }
    }
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        if let Ok(mut child) = self.child.lock() {
            if let Some(child) = child.take() {
                match child {
                    BackendChild::System(mut child) => {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                    BackendChild::Sidecar(child) => {
                        let _ = child.kill();
                    }
                }
            }
        }
    }
}

enum BackendChild {
    System(Child),
    Sidecar(CommandChild),
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let host = env::var("KAJAS_DESKTOP_HOST").unwrap_or_else(|_| "127.0.0.1".into());
            let port = env::var("KAJAS_DESKTOP_PORT")
                .ok()
                .and_then(|value| value.parse::<u16>().ok())
                .unwrap_or(8765);

            if !backend_is_ready(&host, port) {
                let backend = start_backend(app, &host, port)?;
                app.manage(backend);
                wait_for_backend(&host, port, Duration::from_secs(20))?;
            }

            if !backend_is_ready(&host, port) {
                return Err(io::Error::new(
                    io::ErrorKind::NotConnected,
                    format!("Kajas backend is not responding on {host}:{port}"),
                )
                .into());
            }

            let app_url = frontend_url(&host, port);
            let allowed_origin = url_origin(&app_url);
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

fn start_backend(app: &mut tauri::App, host: &str, port: u16) -> SetupResult<BackendProcess> {
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
    let frontend_dist = frontend_dist_path(app, &frontend_dir)?;

    if env::var("KAJAS_BACKEND_CMD").is_err() {
        if let Ok(backend) = start_sidecar_backend(app, host, port, &frontend_dist) {
            return Ok(backend);
        }
    }

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
    Ok(BackendProcess::new(BackendChild::System(child)))
}

fn start_sidecar_backend(
    app: &mut tauri::App,
    host: &str,
    port: u16,
    frontend_dist: &Path,
) -> SetupResult<BackendProcess> {
    let args = vec![
        "serve".to_string(),
        "--host".to_string(),
        host.to_string(),
        "--port".to_string(),
        port.to_string(),
        "--frontend-dir".to_string(),
        frontend_dist.to_string_lossy().into_owned(),
    ];
    let (_, child) = app
        .shell()
        .sidecar("kajas-backend")?
        .args(args)
        .spawn()?;

    Ok(BackendProcess::new(BackendChild::Sidecar(child)))
}

fn frontend_dist_path(app: &tauri::App, frontend_dir: &Path) -> SetupResult<PathBuf> {
    let source_dist = frontend_dir.join("dist");
    if source_dist.exists() {
        return Ok(source_dist);
    }

    Ok(app.path().resource_dir()?.join("dist"))
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

fn frontend_url(host: &str, port: u16) -> String {
    if let Ok(url) = env::var("KAJAS_FRONTEND_URL") {
        return url;
    }
    if cfg!(debug_assertions) {
        return "http://127.0.0.1:5173/".into();
    }
    format!("http://{host}:{port}/")
}

fn url_origin(url: &str) -> String {
    let without_path = url
        .split_once("://")
        .and_then(|(scheme, rest)| {
            let authority = rest.split('/').next()?;
            Some(format!("{scheme}://{authority}"))
        });
    without_path.unwrap_or_else(|| url.trim_end_matches('/').to_string())
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

fn backend_is_ready(host: &str, port: u16) -> bool {
    let url = format!("http://{host}:{port}/api/auth/status");
    let body = match ureq::get(&url)
        .timeout(Duration::from_millis(800))
        .call()
    {
        Ok(response) => match response.into_string() {
            Ok(body) => body,
            Err(_) => return false,
        },
        Err(_) => return false,
    };
    body.contains("\"enabled\"") && body.contains("\"bootstrap_required\"")
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
