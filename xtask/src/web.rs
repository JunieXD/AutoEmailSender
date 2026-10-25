//! Development-only HTTP host and Vite supervisor.

use std::future::IntoFuture;
use std::process::Stdio;
use std::sync::Arc;

use axum::Json;
use axum::Router;
use axum::extract::{DefaultBodyLimit, Request, State};
use axum::http::{HeaderValue, Method, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use serde::Deserialize;
use serde_json::Value;
use tokio::net::TcpListener;
use tokio::process::Command;

pub async fn run() -> crate::Result {
    let dir = crate::root().join(".dev-data");
    let _lock = api::startup::DataLock::acquire(&dir).map_err(|e| e.message())?;
    let context = api::startup::open(&dir).await.map_err(|e| e.message())?;
    let listener = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).await?;
    let api_url = format!("http://{}", listener.local_addr()?);
    let vite_listener = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).await?;
    let vite_port = vite_listener.local_addr()?.port().to_string();
    let origin = format!("http://127.0.0.1:{vite_port}");
    let token = uuid::Uuid::new_v4().simple().to_string();
    let authorization = format!("Bearer {token}");
    let router = Router::new()
        .route(
            "/call",
            post(call).options(|| async { StatusCode::NO_CONTENT }),
        )
        // A material upload is base64 encoded and may contain up to 20 MiB.
        .layer(DefaultBodyLimit::max(28 * 1024 * 1024))
        .layer(middleware::from_fn_with_state(
            (authorization, HeaderValue::from_str(&origin)?),
            authorize,
        ))
        .with_state(Arc::new(context));
    drop(vite_listener);
    let mut vite = Command::new("node")
        .arg(crate::root().join("ui/node_modules/vite/bin/vite.js"))
        .args([
            "--mode",
            "preview",
            "--host",
            "127.0.0.1",
            "--port",
            &vite_port,
            "--strictPort",
        ])
        .current_dir(crate::root().join("ui"))
        .env("AES_PREVIEW_API", api_url)
        .env("AES_PREVIEW_DATA", dir.canonicalize()?)
        .env("AES_PREVIEW_VERSION", env!("CARGO_PKG_VERSION"))
        .stdin(Stdio::null())
        .kill_on_drop(true)
        .spawn()?;
    println!("浏览器预览：{origin}/#preview-token={token}");
    println!(
        "使用 {}，后台工作未启动。按 Ctrl+C 停止。",
        dir.canonicalize()?.display()
    );
    let result = tokio::select! {
        result = axum::serve(listener, router).into_future() => result.map_err(Into::into),
        status = vite.wait() => Err(format!("预览界面服务已退出：{}", status?).into()),
        result = shutdown() => result.map_err(Into::into),
    };
    if vite.try_wait()?.is_none() {
        vite.kill().await?;
    }
    result
}

async fn shutdown() -> std::io::Result<()> {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        tokio::select! {
            result = tokio::signal::ctrl_c() => result,
            _ = terminate.recv() => Ok(()),
        }
    }
    #[cfg(not(unix))]
    tokio::signal::ctrl_c().await
}

async fn authorize(
    State((authorization, origin)): State<(String, HeaderValue)>,
    request: Request,
    next: Next,
) -> Response {
    let headers = request.headers();
    if headers.get(header::ORIGIN) != Some(&origin) {
        return denied("请求来源不是本次预览界面。");
    }
    // Browser preflights do not carry credentials; only the POST may reach the core.
    if request.method() != Method::OPTIONS
        && headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            != Some(authorization.as_str())
    {
        return cors(denied("预览令牌无效，请打开终端打印的完整网址。"), origin);
    }
    cors(next.run(request).await, origin)
}

fn cors(mut response: Response, origin: HeaderValue) -> Response {
    let headers = response.headers_mut();
    headers.insert(header::ACCESS_CONTROL_ALLOW_ORIGIN, origin);
    headers.insert(
        header::ACCESS_CONTROL_ALLOW_METHODS,
        HeaderValue::from_static("POST"),
    );
    headers.insert(
        header::ACCESS_CONTROL_ALLOW_HEADERS,
        HeaderValue::from_static("authorization, content-type"),
    );
    response
}

fn denied(message: &str) -> Response {
    (
        StatusCode::FORBIDDEN,
        Json(api::CallError {
            code: api::ErrorCode::Invalid,
            message: message.into(),
        }),
    )
        .into_response()
}

#[derive(Deserialize)]
struct Call {
    op: String,
    input: Value,
}

async fn call(State(context): State<Arc<api::Context>>, Json(request): Json<Call>) -> Response {
    if request.op == "receiveNow" {
        return (
            StatusCode::BAD_REQUEST,
            Json(api::CallError {
                code: api::ErrorCode::Invalid,
                message: "浏览器预览未启动收信，请在桌面开发版里收信。".into(),
            }),
        )
            .into_response();
    }
    match api::call(&context, &request.op, request.input).await {
        Ok(output) => Json(output).into_response(),
        Err(error) => (StatusCode::BAD_REQUEST, Json(error)).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::{Body, to_bytes};
    use tower::ServiceExt;

    #[tokio::test]
    async fn only_the_preview_origin_with_the_token_can_call_the_core() -> crate::Result {
        let origin = "http://127.0.0.1:49152";
        let origin_header = HeaderValue::from_str(origin)?;
        let router = Router::new()
            .route(
                "/call",
                post(|| async { "called" }).options(|| async { StatusCode::NO_CONTENT }),
            )
            .layer(middleware::from_fn_with_state(
                ("Bearer test-token".to_owned(), origin_header.clone()),
                authorize,
            ));
        for (request_origin, authorization, expected) in [
            (Some(origin), Some("Bearer test-token"), StatusCode::OK),
            (Some(origin), None, StatusCode::FORBIDDEN),
            (
                Some(origin),
                Some("Bearer wrong-token"),
                StatusCode::FORBIDDEN,
            ),
            (
                Some("http://127.0.0.1:49153"),
                Some("Bearer test-token"),
                StatusCode::FORBIDDEN,
            ),
            (
                Some("https://other.example"),
                Some("Bearer test-token"),
                StatusCode::FORBIDDEN,
            ),
            (
                Some("null"),
                Some("Bearer test-token"),
                StatusCode::FORBIDDEN,
            ),
            (None, Some("Bearer test-token"), StatusCode::FORBIDDEN),
        ] {
            let mut request = Request::builder().method(Method::POST).uri("/call");
            if let Some(request_origin) = request_origin {
                request = request.header(header::ORIGIN, request_origin);
            }
            if let Some(authorization) = authorization {
                request = request.header(header::AUTHORIZATION, authorization);
            }
            let response = router.clone().oneshot(request.body(Body::empty())?).await?;
            assert_eq!(response.status(), expected);
            assert_eq!(
                response.headers().get(header::ACCESS_CONTROL_ALLOW_ORIGIN),
                (request_origin == Some(origin)).then_some(&origin_header),
            );
            if expected == StatusCode::OK {
                assert_eq!(to_bytes(response.into_body(), 1024).await?, "called");
            } else {
                let error: Value =
                    serde_json::from_slice(&to_bytes(response.into_body(), 1024).await?)?;
                assert_eq!(error["code"], "invalid");
            }
        }
        let preflight = Request::builder()
            .method(Method::OPTIONS)
            .uri("/call")
            .header(header::ORIGIN, origin)
            .header(header::ACCESS_CONTROL_REQUEST_METHOD, "POST")
            .header(
                header::ACCESS_CONTROL_REQUEST_HEADERS,
                "authorization, content-type",
            )
            .body(Body::empty())?;
        let response = router.oneshot(preflight).await?;
        assert_eq!(response.status(), StatusCode::NO_CONTENT);
        assert_eq!(
            response.headers()[header::ACCESS_CONTROL_ALLOW_ORIGIN],
            origin
        );
        Ok(())
    }
}
