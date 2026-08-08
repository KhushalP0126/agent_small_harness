use std::io::Write;

use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use image::{imageops::FilterType, RgbaImage};
use ratatui::{
    layout::Rect,
    style::{Color, Style},
    text::{Line, Span, Text},
    widgets::Paragraph,
    Frame,
};

const DIAGRAM_BACKGROUND: [u8; 4] = [20, 20, 20, 255];
const KITTY_IMAGE_ID: u32 = 31;

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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RenderMode {
    Kitty,
    Iterm2,
    QuadBlocks,
}

impl RenderMode {
    fn detect() -> Self {
        Self::from_environment(
            std::env::var("TERM_PROGRAM").ok().as_deref(),
            std::env::var("TERM").ok().as_deref(),
            std::env::var_os("KITTY_WINDOW_ID").is_some(),
            std::env::var_os("WEZTERM_EXECUTABLE").is_some(),
        )
    }

    fn from_environment(
        term_program: Option<&str>,
        term: Option<&str>,
        kitty_window: bool,
        wezterm: bool,
    ) -> Self {
        if kitty_window
            || term.is_some_and(|value| value.contains("xterm-kitty") || value.contains("ghostty"))
            || term_program.is_some_and(|value| value.eq_ignore_ascii_case("ghostty"))
        {
            Self::Kitty
        } else if wezterm
            || term_program.is_some_and(|value| {
                value.eq_ignore_ascii_case("iTerm.app") || value.eq_ignore_ascii_case("WezTerm")
            })
        {
            Self::Iterm2
        } else {
            Self::QuadBlocks
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Kitty => "Kitty graphics (native)",
            Self::Iterm2 => "iTerm2 inline images (native)",
            Self::QuadBlocks => "quadrant-block fallback (2x2 pixels/cell)",
        }
    }
}

pub struct MermaidView {
    png: Option<Vec<u8>>,
    rgba: Option<RgbaImage>,
    visible: bool,
    error: Option<String>,
    host_os: HostOs,
    terminal_name: String,
    mode: RenderMode,
    viewport: Option<Rect>,
    protocol_dirty: bool,
    kitty_clear_pending: bool,
    quad_cache: Option<(u16, u16, Text<'static>)>,
}

impl MermaidView {
    pub fn new() -> Self {
        Self::from_parts(
            HostOs::detect(),
            detect_terminal_name(),
            RenderMode::detect(),
        )
    }

    fn from_parts(host_os: HostOs, terminal_name: String, mode: RenderMode) -> Self {
        Self {
            png: None,
            rgba: None,
            visible: false,
            error: None,
            host_os,
            terminal_name,
            mode,
            viewport: None,
            protocol_dirty: false,
            kitty_clear_pending: false,
            quad_cache: None,
        }
    }

    pub fn set_diagram(&mut self, source: &str) -> Result<()> {
        match render_png(source) {
            Ok(png) => {
                let rgba = image::load_from_memory(&png)
                    .context("decode rendered diagram")?
                    .into_rgba8();
                self.png = Some(png);
                self.rgba = Some(rgba);
                self.error = None;
                self.protocol_dirty = true;
                self.quad_cache = None;
                Ok(())
            }
            Err(error) => {
                self.error = Some(error.to_string());
                Err(error)
            }
        }
    }

    pub fn show(&mut self) {
        self.visible = true;
        self.protocol_dirty = true;
    }

    pub fn hide(&mut self) {
        self.visible = false;
        self.viewport = None;
        self.kitty_clear_pending = self.mode == RenderMode::Kitty;
    }

    pub fn is_visible(&self) -> bool {
        self.visible
    }

    pub fn error(&self) -> Option<&str> {
        self.error.as_deref()
    }

    pub fn status_label(&self) -> String {
        format!(
            "{} · {} · {}",
            self.host_os.label(),
            self.terminal_name,
            self.mode.label()
        )
    }

    pub fn uses_text_fallback(&self) -> bool {
        self.mode == RenderMode::QuadBlocks
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
        self.protocol_dirty = true;
        proposed
    }

    pub fn render(&mut self, frame: &mut Frame, area: Rect) {
        if !self.visible || self.mode != RenderMode::QuadBlocks {
            return;
        }
        let Some(image) = &self.rgba else {
            return;
        };
        let needs_refresh = self
            .quad_cache
            .as_ref()
            .is_none_or(|(width, height, _)| *width != area.width || *height != area.height);
        if needs_refresh {
            self.quad_cache = Some((
                area.width,
                area.height,
                quadblock_text(image, area.width, area.height),
            ));
        }
        if let Some((_, _, text)) = &self.quad_cache {
            frame.render_widget(Paragraph::new(text.clone()), area);
        }
    }

    pub fn write_protocol<W: Write>(&mut self, writer: &mut W) -> Result<()> {
        if self.kitty_clear_pending {
            writer.write_all(format!("\x1b_Ga=d,d=i,i={KITTY_IMAGE_ID},q=2\x1b\\").as_bytes())?;
            writer.flush()?;
            self.kitty_clear_pending = false;
        }
        if !self.visible || !self.protocol_dirty || self.mode == RenderMode::QuadBlocks {
            return Ok(());
        }
        let (Some(png), Some(area)) = (&self.png, self.viewport) else {
            return Ok(());
        };
        match self.mode {
            RenderMode::Kitty => writer.write_all(&kitty_sequence(png, area))?,
            RenderMode::Iterm2 => writer.write_all(&iterm2_sequence(png, area))?,
            RenderMode::QuadBlocks => {}
        }
        writer.flush()?;
        self.protocol_dirty = false;
        Ok(())
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

fn kitty_sequence(png: &[u8], area: Rect) -> Vec<u8> {
    let encoded = BASE64.encode(png);
    let mut output = format!(
        "\x1b[{};{}H\x1b_Ga=d,d=i,i={KITTY_IMAGE_ID},q=2\x1b\\",
        area.y + 1,
        area.x + 1
    )
    .into_bytes();
    let chunks: Vec<&[u8]> = encoded.as_bytes().chunks(4096).collect();
    for (index, chunk) in chunks.iter().enumerate() {
        let more = usize::from(index + 1 < chunks.len());
        if index == 0 {
            output.extend_from_slice(
                format!(
                    "\x1b_Ga=T,f=100,t=d,i={KITTY_IMAGE_ID},q=2,c={},r={},m={more};",
                    area.width, area.height
                )
                .as_bytes(),
            );
        } else {
            output.extend_from_slice(format!("\x1b_Gm={more};").as_bytes());
        }
        output.extend_from_slice(chunk);
        output.extend_from_slice(b"\x1b\\");
    }
    output
}

fn iterm2_sequence(png: &[u8], area: Rect) -> Vec<u8> {
    format!(
        "\x1b[{};{}H\x1b]1337;File=inline=1;width={};height={};preserveAspectRatio=1:{}\x07",
        area.y + 1,
        area.x + 1,
        area.width,
        area.height,
        BASE64.encode(png)
    )
    .into_bytes()
}

fn quadblock_text(image: &RgbaImage, cols: u16, rows: u16) -> Text<'static> {
    if cols == 0 || rows == 0 {
        return Text::default();
    }
    let max_width = u32::from(cols) * 2;
    let max_height = u32::from(rows) * 2;
    let scale =
        (max_width as f64 / image.width() as f64).min(max_height as f64 / image.height() as f64);
    let width = ((image.width() as f64 * scale).round() as u32).clamp(1, max_width);
    let height = ((image.height() as f64 * scale).round() as u32).clamp(1, max_height);
    let resized = image::imageops::resize(image, width, height, FilterType::Triangle);
    let rendered_cols = width.div_ceil(2) as u16;
    let rendered_rows = height.div_ceil(2) as u16;
    let x_offset = (cols.saturating_sub(rendered_cols)) / 2;
    let y_offset = (rows.saturating_sub(rendered_rows)) / 2;
    let background = Color::Rgb(
        DIAGRAM_BACKGROUND[0],
        DIAGRAM_BACKGROUND[1],
        DIAGRAM_BACKGROUND[2],
    );
    let mut lines = Vec::with_capacity(rows as usize);
    for row in 0..rows {
        let mut spans = Vec::with_capacity(cols as usize);
        for col in 0..cols {
            if row < y_offset
                || row >= y_offset + rendered_rows
                || col < x_offset
                || col >= x_offset + rendered_cols
            {
                spans.push(Span::styled(" ", Style::default().bg(background)));
                continue;
            }
            let pixel_x = u32::from(col - x_offset) * 2;
            let pixel_y = u32::from(row - y_offset) * 2;
            let pixels = [
                sample_rgb(&resized, pixel_x, pixel_y),
                sample_rgb(&resized, pixel_x + 1, pixel_y),
                sample_rgb(&resized, pixel_x, pixel_y + 1),
                sample_rgb(&resized, pixel_x + 1, pixel_y + 1),
            ];
            let (glyph, foreground, cell_background) = quadrant_cell(pixels);
            spans.push(Span::styled(
                glyph.to_string(),
                Style::default()
                    .fg(Color::Rgb(foreground[0], foreground[1], foreground[2]))
                    .bg(Color::Rgb(
                        cell_background[0],
                        cell_background[1],
                        cell_background[2],
                    )),
            ));
        }
        lines.push(Line::from(spans));
    }
    Text::from(lines)
}

fn sample_rgb(image: &RgbaImage, x: u32, y: u32) -> [u8; 3] {
    let pixel = image.get_pixel(
        x.min(image.width().saturating_sub(1)),
        y.min(image.height().saturating_sub(1)),
    );
    [pixel[0], pixel[1], pixel[2]]
}

fn quadrant_cell(pixels: [[u8; 3]; 4]) -> (char, [u8; 3], [u8; 3]) {
    let darkest = *pixels.iter().min_by_key(luminance).expect("four pixels");
    let brightest = *pixels.iter().max_by_key(luminance).expect("four pixels");
    if darkest == brightest {
        return (' ', brightest, darkest);
    }
    let mut mask = 0_u8;
    let mut foreground_members = Vec::new();
    let mut background_members = Vec::new();
    for (index, pixel) in pixels.iter().enumerate() {
        if color_distance(pixel, &brightest) <= color_distance(pixel, &darkest) {
            mask |= 1 << index;
            foreground_members.push(*pixel);
        } else {
            background_members.push(*pixel);
        }
    }
    let foreground = average_color(&foreground_members, brightest);
    let background = average_color(&background_members, darkest);
    (quadrant_glyph(mask), foreground, background)
}

fn luminance(pixel: &&[u8; 3]) -> u32 {
    2126 * u32::from(pixel[0]) + 7152 * u32::from(pixel[1]) + 722 * u32::from(pixel[2])
}

fn color_distance(left: &[u8; 3], right: &[u8; 3]) -> u32 {
    left.iter()
        .zip(right)
        .map(|(a, b)| i32::from(*a).abs_diff(i32::from(*b)))
        .sum()
}

fn average_color(values: &[[u8; 3]], fallback: [u8; 3]) -> [u8; 3] {
    if values.is_empty() {
        return fallback;
    }
    let mut sums = [0_u32; 3];
    for value in values {
        for channel in 0..3 {
            sums[channel] += u32::from(value[channel]);
        }
    }
    let count = values.len() as u32;
    [
        (sums[0] / count) as u8,
        (sums[1] / count) as u8,
        (sums[2] / count) as u8,
    ]
}

fn quadrant_glyph(mask: u8) -> char {
    [
        ' ', '▘', '▝', '▀', '▖', '▌', '▞', '▛', '▗', '▚', '▐', '▜', '▄', '▙', '▟', '█',
    ][usize::from(mask)]
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

    #[test]
    fn flowchart_renders_to_png() {
        let png = render_png("flowchart LR\n  A[Start] --> B[Done]").unwrap();
        assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));
    }

    #[test]
    fn unsupported_source_fails_without_panicking() {
        assert!(render_png("definitelyNotMermaid\n  what").is_err());
    }

    #[test]
    fn rendered_png_has_an_opaque_dark_background() {
        let png = render_png("flowchart TD\n  A --> B").unwrap();
        let image = image::load_from_memory(&png).unwrap().into_rgba8();
        assert_eq!(image.get_pixel(0, 0).0, DIAGRAM_BACKGROUND);
    }

    #[test]
    fn renderer_detection_prefers_native_protocols() {
        assert_eq!(
            RenderMode::from_environment(Some("iTerm.app"), Some("xterm-256color"), false, false),
            RenderMode::Iterm2
        );
        assert_eq!(
            RenderMode::from_environment(None, Some("xterm-kitty"), false, false),
            RenderMode::Kitty
        );
        assert_eq!(
            RenderMode::from_environment(Some("ghostty"), Some("xterm-ghostty"), false, false),
            RenderMode::Kitty
        );
        assert_eq!(
            RenderMode::from_environment(Some("Apple_Terminal"), Some("xterm"), false, false),
            RenderMode::QuadBlocks
        );
    }

    #[test]
    fn native_protocol_sequences_include_position_and_payload() {
        let area = Rect::new(4, 2, 80, 24);
        let iterm = String::from_utf8(iterm2_sequence(b"png", area)).unwrap();
        assert!(iterm.contains("\x1b[3;5H"));
        assert!(iterm.contains("File=inline=1;width=80;height=24"));
        let kitty = String::from_utf8(kitty_sequence(b"png", area)).unwrap();
        assert!(kitty.contains("a=T,f=100"));
        assert!(kitty.contains("c=80,r=24"));
    }

    #[test]
    fn quadrant_mask_maps_all_four_pixels() {
        assert_eq!(quadrant_glyph(0), ' ');
        assert_eq!(quadrant_glyph(0b0011), '▀');
        assert_eq!(quadrant_glyph(0b1100), '▄');
        assert_eq!(quadrant_glyph(0b1111), '█');
    }

    #[test]
    fn quadblock_renderer_matches_terminal_dimensions() {
        let image = RgbaImage::from_pixel(8, 8, image::Rgba([255, 255, 255, 255]));
        let text = quadblock_text(&image, 6, 4);
        assert_eq!(text.lines.len(), 4);
        assert!(text.lines.iter().all(|line| line.width() == 6));
    }

    #[test]
    fn viewport_ignores_one_cell_terminal_jitter() {
        let mut view =
            MermaidView::from_parts(HostOs::MacOs, "test".into(), RenderMode::QuadBlocks);
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
