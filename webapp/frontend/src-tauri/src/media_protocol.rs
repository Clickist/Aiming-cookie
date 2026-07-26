use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::RwLock;
use tauri::http::{
    header::{
        ACCEPT_RANGES, ACCESS_CONTROL_ALLOW_ORIGIN, ACCESS_CONTROL_EXPOSE_HEADERS, CONTENT_LENGTH,
        CONTENT_RANGE, CONTENT_TYPE, RANGE,
    },
    Method, Request, Response, StatusCode,
};

const MAX_RANGE_BYTES: u64 = 1024 * 1000;
const UNAVAILABLE_BODY: &[u8] = br#"{"schema_version":"managed_video_unavailable.v1","availability":"unavailable","reason":"managed_video_unavailable"}"#;
const INVALID_BODY: &[u8] =
    br#"{"schema_version":"managed_video_request_error.v1","error":"invalid_analysis_ref"}"#;

#[derive(Default)]
pub struct ManagedMediaProtocol {
    app_data_dir: RwLock<Option<PathBuf>>,
}

impl ManagedMediaProtocol {
    pub fn configure(&self, app_data_dir: PathBuf) -> Result<(), String> {
        *self
            .app_data_dir
            .write()
            .map_err(|_| "managed media state is unavailable".to_string())? = Some(app_data_dir);
        Ok(())
    }

    pub fn response(&self, request: Request<Vec<u8>>) -> Response<Vec<u8>> {
        let session_id = match parse_analysis_id(request.uri().path()) {
            Some(value) => value,
            None => return json_response(StatusCode::BAD_REQUEST, INVALID_BODY),
        };
        let root = match self.app_data_dir.read() {
            Ok(value) => value.clone(),
            Err(_) => None,
        };
        let Some(root) = root else {
            return json_response(StatusCode::SERVICE_UNAVAILABLE, UNAVAILABLE_BODY);
        };
        let path = root
            .join("sessions")
            .join(session_id.to_string())
            .join("video.mp4");
        match video_response(&request, &root, &path) {
            Ok(response) => response,
            Err(MediaError::Unavailable) => json_response(StatusCode::GONE, UNAVAILABLE_BODY),
            Err(MediaError::Invalid) => json_response(StatusCode::BAD_REQUEST, INVALID_BODY),
        }
    }
}

enum MediaError {
    Invalid,
    Unavailable,
}

fn parse_analysis_id(path: &str) -> Option<u64> {
    let mut parts = path.trim_matches('/').split('/');
    if parts.next()? != "analysis" {
        return None;
    }
    let raw = parts.next()?;
    if raw.is_empty() || !raw.bytes().all(|byte| byte.is_ascii_digit()) || parts.next().is_some() {
        return None;
    }
    raw.parse::<u64>().ok().filter(|value| *value > 0)
}

fn video_response(
    request: &Request<Vec<u8>>,
    app_data_dir: &Path,
    path: &Path,
) -> Result<Response<Vec<u8>>, MediaError> {
    let metadata = path
        .symlink_metadata()
        .map_err(|_| MediaError::Unavailable)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(MediaError::Unavailable);
    }
    let canonical_root = app_data_dir
        .canonicalize()
        .map_err(|_| MediaError::Unavailable)?;
    let canonical_path = path.canonicalize().map_err(|_| MediaError::Unavailable)?;
    if !canonical_path.starts_with(canonical_root.join("sessions")) {
        return Err(MediaError::Invalid);
    }
    let mut file = File::open(canonical_path).map_err(|_| MediaError::Unavailable)?;
    let len = metadata.len();
    let builder = Response::builder()
        .header(CONTENT_TYPE, "video/mp4")
        .header(ACCEPT_RANGES, "bytes")
        .header(ACCESS_CONTROL_ALLOW_ORIGIN, "*");

    if request.method() == Method::HEAD {
        return builder
            .header(CONTENT_LENGTH, len)
            .body(Vec::new())
            .map_err(|_| MediaError::Invalid);
    }
    if let Some(header) = request.headers().get(RANGE) {
        let header = header.to_str().map_err(|_| MediaError::Invalid)?;
        let (start, end) = parse_single_range(header, len)?;
        let capped_end = end.min(start + MAX_RANGE_BYTES - 1);
        let count = capped_end + 1 - start;
        let mut body = Vec::with_capacity(count as usize);
        file.seek(SeekFrom::Start(start))
            .map_err(|_| MediaError::Unavailable)?;
        file.take(count)
            .read_to_end(&mut body)
            .map_err(|_| MediaError::Unavailable)?;
        return builder
            .status(StatusCode::PARTIAL_CONTENT)
            .header(CONTENT_RANGE, format!("bytes {start}-{capped_end}/{len}"))
            .header(CONTENT_LENGTH, count)
            .header(ACCESS_CONTROL_EXPOSE_HEADERS, "content-range")
            .body(body)
            .map_err(|_| MediaError::Invalid);
    }
    let mut body = Vec::with_capacity(len as usize);
    file.read_to_end(&mut body)
        .map_err(|_| MediaError::Unavailable)?;
    builder
        .header(CONTENT_LENGTH, len)
        .body(body)
        .map_err(|_| MediaError::Invalid)
}

fn parse_single_range(value: &str, len: u64) -> Result<(u64, u64), MediaError> {
    let value = value.strip_prefix("bytes=").ok_or(MediaError::Invalid)?;
    if value.contains(',') || len == 0 {
        return Err(MediaError::Invalid);
    }
    let (start, end) = value.split_once('-').ok_or(MediaError::Invalid)?;
    let (start, end) = if start.is_empty() {
        let suffix = end.parse::<u64>().map_err(|_| MediaError::Invalid)?;
        if suffix == 0 {
            return Err(MediaError::Invalid);
        }
        (len.saturating_sub(suffix.min(len)), len - 1)
    } else {
        let start = start.parse::<u64>().map_err(|_| MediaError::Invalid)?;
        let end = if end.is_empty() {
            len - 1
        } else {
            end.parse::<u64>().map_err(|_| MediaError::Invalid)?
        };
        (start, end)
    };
    if start >= len || end < start || end >= len {
        return Err(MediaError::Invalid);
    }
    Ok((start, end))
}

fn json_response(status: StatusCode, body: &[u8]) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, "application/json")
        .header(ACCESS_CONTROL_ALLOW_ORIGIN, "*")
        .body(body.to_vec())
        .expect("static managed media response is valid")
}

#[cfg(test)]
mod tests {
    use super::*;
    use tauri::http::header::{CONTENT_RANGE, RANGE};

    fn fixture() -> (PathBuf, ManagedMediaProtocol) {
        let root =
            std::env::temp_dir().join(format!("aiming-cookie-media-{}", rand::random::<u64>()));
        let video = root.join("sessions").join("42").join("video.mp4");
        std::fs::create_dir_all(video.parent().unwrap()).unwrap();
        std::fs::write(video, b"0123456789").unwrap();
        let protocol = ManagedMediaProtocol::default();
        protocol.configure(root.clone()).unwrap();
        (root, protocol)
    }

    #[test]
    fn path_free_analysis_route_supports_byte_ranges() {
        let (root, protocol) = fixture();
        let request = Request::builder()
            .uri("aiming-cookie-media://localhost/analysis/42")
            .header(RANGE, "bytes=2-5")
            .body(Vec::new())
            .unwrap();

        let response = protocol.response(request);

        assert_eq!(response.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(response.headers()[CONTENT_RANGE], "bytes 2-5/10");
        assert_eq!(response.body(), b"2345");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unavailable_and_invalid_routes_never_echo_local_paths() {
        let (root, protocol) = fixture();
        let missing = protocol.response(
            Request::builder()
                .uri("aiming-cookie-media://localhost/analysis/99")
                .body(Vec::new())
                .unwrap(),
        );
        let invalid = protocol.response(
            Request::builder()
                .uri("aiming-cookie-media://localhost/analysis/not-a-number")
                .body(Vec::new())
                .unwrap(),
        );
        let serialized = format!(
            "{}{}",
            String::from_utf8_lossy(missing.body()),
            String::from_utf8_lossy(invalid.body()),
        );

        assert_eq!(missing.status(), StatusCode::GONE);
        assert_eq!(invalid.status(), StatusCode::BAD_REQUEST);
        assert!(!serialized.contains(&root.display().to_string()));
        assert!(String::from_utf8_lossy(missing.body()).contains("managed_video_unavailable.v1"));
        std::fs::remove_dir_all(root).unwrap();
    }
}
