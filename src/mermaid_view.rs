use anyhow::{anyhow, Context, Result};
use ratatui::{layout::Rect, Frame};
use ratatui_image::{
    picker::Picker,
    protocol::StatefulProtocol,
    StatefulImage,
};

pub struct MermaidView {
    picker: Picker,
    image: Option<StatefulProtocol>,
    visible: bool,
    error: Option<String>,
}

impl MermaidView {
    pub fn new() -> Result<Self> {
        // Query modern terminal protocols when possible; a plain terminal or
        // test runner may not answer, so retain ratatui-image's half-block
        // compatible picker instead of making the entire TUI fail to start.
        let picker =
            Picker::from_query_stdio().unwrap_or_else(|_| Picker::from_fontsize((8, 16)));
        Ok(Self::with_picker(picker))
    }

    pub fn with_picker(picker: Picker) -> Self {
        Self {
            picker,
            image: None,
            visible: false,
            error: None,
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

    pub fn render(&mut self, frame: &mut Frame, area: Rect) {
        if !self.visible {
            return;
        }
        if let Some(image) = &mut self.image {
            frame.render_stateful_widget(StatefulImage::default(), area, image);
        }
    }
}

fn render_png(source: &str) -> Result<Vec<u8>> {
    let svg = mermaid_rs_renderer::render(source).context("render Mermaid to SVG")?;
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
}
