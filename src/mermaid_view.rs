use anyhow::{anyhow, Context, Result};
use ratatui::{layout::Rect, Frame};
use ratatui_image::{
    picker::{Picker, ProtocolType},
    protocol::StatefulProtocol,
    StatefulImage,
};

const FALLBACK_FONT_SIZE: (u16, u16) = (8, 16);
const DIAGRAM_BACKGROUND: [u8; 4] = [20, 20, 20, 255];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostOs {
    MacOs,
    Windows,
    Linux,
    Other,
}

impl HostOs {
    fn detect() -> Self {
        Self::from_name(std::env::consts::OS)
    }

    fn from_name(name: &str) -> Self {
        match name {
            "macos" => Self::MacOs,
            "windows" => Self::Windows,
            "linux" => Self::Linux,
            _ => Self::Other,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::MacOs => "macOS",
            Self::Windows => "Windows",
            Self::Linux => "Linux",
            Self::Other => "Other OS",
        }
    }
}

pub struct MermaidView {
    picker: Picker,
    image: Option<StatefulProtocol>,
    visible: bool,
    error: Option<String>,
    host_os: HostOs,
    terminal_name: String,
    viewport: Option<Rect>,
}

impl MermaidView {
    pub fn new() -> Self {
        let host_os = HostOs::detect();
        // ratatui-image 8 documents direct stdio capability queries as
        // unavailable on Windows. Its environment-aware fontsize constructor
        // still detects terminals such as WezTerm; macOS and Linux can perform
        // the live Kitty/Sixel/iTerm2 query.
        #[cfg(target_os = "windows")]
        let mut picker = Picker::from_fontsize(FALLBACK_FONT_SIZE);
        #[cfg(not(target_os = "windows"))]
        let mut picker = Picker::from_query_stdio()
            .unwrap_or_else(|_| Picker::from_fontsize(FALLBACK_FONT_SIZE));
        picker.set_background_color(image::Rgba(DIAGRAM_BACKGROUND));

        Self::with_picker_and_os(picker, host_os)
    }

    #[cfg(test)]
    fn with_picker(mut picker: Picker) -> Self {
        picker.set_background_color(image::Rgba(DIAGRAM_BACKGROUND));
        Self::with_picker_and_os(picker, HostOs::detect())
    }

    fn with_picker_and_os(picker: Picker, host_os: HostOs) -> Self {
        Self::from_parts(picker, host_os, detect_terminal_name())
    }

    fn from_parts(picker: Picker, host_os: HostOs, terminal_name: String) -> Self {
        Self {
            picker,
            image: None,
            visible: false,
            error: None,
            host_os,
            terminal_name,
            viewport: None,
        }
    }

    pub fn set_diagram(&mut self, source: &str) -> Result<()> {
        match render_png(source) {
            Ok(png) => {
                let image = image::load_from_memory(&png).context("decode rendered diagram")?;
                self.image = Some(self.picker.new_resize_protocol(image));
                self.error = None;
                Ok(())
            }
            Err(error) => {
                self.error = Some(error.to_string());
                Err(error)
            }
        }
    }

    pub fn toggle(&mut self) {
        self.visible = !self.visible;
        if !self.visible {
            self.viewport = None;
        }
    }

    pub fn show(&mut self) {
        self.visible = true;
    }

    pub fn is_visible(&self) -> bool {
        self.visible
    }

    pub fn error(&self) -> Option<&str> {
        self.error.as_deref()
    }

    pub fn status_label(&self) -> String {
        let protocol = match self.picker.protocol_type() {
            ProtocolType::Halfblocks => "half-block fallback (low resolution)",
            ProtocolType::Sixel => "Sixel",
            ProtocolType::Kitty => "Kitty graphics",
            ProtocolType::Iterm2 => "iTerm2 inline images",
        };
        format!(
            "{} · {} · {protocol}",
            self.host_os.label(),
            self.terminal_name
        )
    }

    pub fn uses_low_resolution_fallback(&self) -> bool {
        self.picker.protocol_type() == ProtocolType::Halfblocks
    }

    pub fn stabilize_viewport(&mut self, proposed: Rect, bounds: Rect) -> Rect {
        if let Some(previous) = self.viewport {
            let small_change = previous.x.abs_diff(proposed.x) <= 1
                && previous.y.abs_diff(proposed.y) <= 1
                && previous.width.abs_diff(proposed.width) <= 2
                && previous.height.abs_diff(proposed.height) <= 2;
            let still_fits =
                previous.right() <= bounds.right() && previous.bottom() <= bounds.bottom();
            if small_change && still_fits {
                return previous;
            }
        }
        self.viewport = Some(proposed);
        proposed
    }

    pub fn render(&mut self, frame: &mut Frame, area: Rect) {
        if !self.visible {
            return;
        }
        if let Some(image) = &mut self.image {
            frame.render_stateful_widget(StatefulImage::default(), area, image);
        }
    }
}

fn detect_terminal_name() -> String {
    if let Ok(name) = std::env::var("TERM_PROGRAM") {
        if !name.trim().is_empty() {
            return name;
        }
    }
    if std::env::var_os("WEZTERM_EXECUTABLE").is_some() {
        return "WezTerm".into();
    }
    if std::env::var_os("KITTY_WINDOW_ID").is_some() {
        return "Kitty".into();
    }
    if std::env::var_os("WT_SESSION").is_some() {
        return "Windows Terminal".into();
    }
    std::env::var("TERM")
        .ok()
        .filter(|name| !name.trim().is_empty())
        .unwrap_or_else(|| "unknown terminal".into())
}

fn render_png(source: &str) -> Result<Vec<u8>> {
    let mut theme = mermaid_rs_renderer::Theme::dark();
    theme.background = "#141414".into();
    let options = mermaid_rs_renderer::RenderOptions {
        theme,
        ..Default::default()
    };
    let svg = mermaid_rs_renderer::render_with_options(source, options)
        .context("render Mermaid to SVG")?;
    svg_to_png(&svg)
}

fn svg_to_png(svg: &str) -> Result<Vec<u8>> {
    let options = resvg::usvg::Options::default();
    let tree = resvg::usvg::Tree::from_str(svg, &options).context("parse rendered SVG")?;
    let size = tree.size().to_int_size();
    if size.width() == 0 || size.height() == 0 {
        return Err(anyhow!("renderer produced an empty SVG"));
    }
    let mut pixmap = resvg::tiny_skia::Pixmap::new(size.width(), size.height())
        .context("allocate PNG canvas")?;
    pixmap.fill(resvg::tiny_skia::Color::from_rgba8(
        DIAGRAM_BACKGROUND[0],
        DIAGRAM_BACKGROUND[1],
        DIAGRAM_BACKGROUND[2],
        DIAGRAM_BACKGROUND[3],
    ));
    resvg::render(
        &tree,
        resvg::tiny_skia::Transform::identity(),
        &mut pixmap.as_mut(),
    );
    pixmap.encode_png().context("encode Mermaid PNG")
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::DynamicImage;

    #[test]
    fn flowchart_renders_to_png() {
        let png = render_png("flowchart LR\n  A[Start] --> B[Done]").unwrap();
        assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));
    }

    #[test]
    fn class_diagram_renders_to_png() {
        let png = render_png("classDiagram\n  class Worker\n  Worker : +run()").unwrap();
        assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));
    }

    #[test]
    fn unsupported_source_fails_without_panicking() {
        assert!(render_png("definitelyNotMermaid\n  what").is_err());
    }

    #[test]
    fn decoded_png_is_a_real_image() {
        let png = render_png("flowchart TD\n  A --> B").unwrap();
        assert!(matches!(
            image::load_from_memory(&png).unwrap(),
            DynamicImage::ImageRgba8(_) | DynamicImage::ImageRgb8(_)
        ));
    }

    #[test]
    fn rendered_png_has_an_opaque_dark_background() {
        let png = render_png("flowchart TD\n  A --> B").unwrap();
        let image = image::load_from_memory(&png).unwrap().into_rgba8();
        assert_eq!(image.get_pixel(0, 0).0, DIAGRAM_BACKGROUND);
    }

    #[test]
    fn viewport_ignores_one_cell_terminal_jitter() {
        let picker = Picker::from_fontsize(FALLBACK_FONT_SIZE);
        let mut view = MermaidView::with_picker(picker);
        let bounds = Rect::new(0, 0, 120, 40);
        let original = Rect::new(8, 4, 104, 32);
        assert_eq!(view.stabilize_viewport(original, bounds), original);
        assert_eq!(
            view.stabilize_viewport(Rect::new(8, 4, 103, 31), bounds),
            original
        );
    }

    #[test]
    fn supported_operating_systems_have_explicit_labels() {
        assert_eq!(HostOs::from_name("macos").label(), "macOS");
        assert_eq!(HostOs::from_name("windows").label(), "Windows");
        assert_eq!(HostOs::from_name("linux").label(), "Linux");
    }
}
