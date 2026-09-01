//! Shared terminal colors and lightweight Markdown rendering.
//!
//! Keeping these presentation-only helpers outside `main.rs` makes the event
//! loop easier to follow and gives rendering behavior a focused test surface.

use ratatui::{
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Padding},
};

pub(crate) fn theme_background() -> Color {
    Color::Rgb(4, 7, 13)
}
pub(crate) fn theme_panel() -> Color {
    Color::Rgb(10, 18, 32)
}
pub(crate) fn theme_status() -> Color {
    Color::Rgb(15, 24, 42)
}
pub(crate) fn theme_foreground() -> Color {
    Color::Rgb(231, 240, 252)
}
pub(crate) fn theme_muted() -> Color {
    Color::Rgb(74, 100, 133)
}
pub(crate) fn theme_border() -> Color {
    Color::Rgb(27, 42, 66)
}
pub(crate) fn theme_cyan() -> Color {
    Color::Rgb(56, 189, 248)
}
pub(crate) fn theme_purple() -> Color {
    Color::Rgb(147, 197, 253)
}
pub(crate) fn theme_you() -> Color {
    Color::Rgb(125, 211, 252)
}
pub(crate) fn theme_assistant() -> Color {
    Color::Rgb(96, 165, 250)
}
pub(crate) fn theme_working() -> Color {
    Color::Rgb(147, 197, 253)
}
pub(crate) fn theme_system() -> Color {
    Color::Rgb(59, 130, 246)
}
pub(crate) fn theme_ready() -> Color {
    Color::Rgb(96, 165, 250)
}

pub(crate) fn pane_block<'a>(title: impl Into<Line<'a>>, active: bool) -> Block<'a> {
    pane_block_accent(title, active, Color::Rgb(71, 85, 105))
}

pub(crate) fn pane_block_accent<'a>(
    title: impl Into<Line<'a>>,
    active: bool,
    accent: Color,
) -> Block<'a> {
    let color = if active { theme_cyan() } else { accent };
    Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(color))
        .style(Style::default().bg(theme_panel()))
        .padding(Padding::horizontal(1))
        .title(title)
        .title_style(Style::default().fg(color).add_modifier(Modifier::BOLD))
}

/// Render the small Markdown subset used by repository documentation.
pub(crate) fn markdown_lines(source: &str) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    let mut in_code_block = false;
    for raw in source.lines() {
        let trimmed = raw.trim();
        if trimmed.starts_with("```") {
            in_code_block = !in_code_block;
            let language = trimmed.trim_start_matches('`').trim();
            lines.push(Line::styled(
                if in_code_block {
                    format!("┌ code {language}")
                } else {
                    "└".into()
                },
                Style::default().fg(theme_muted()),
            ));
            continue;
        }
        if in_code_block {
            lines.push(Line::styled(
                raw.to_owned(),
                Style::default().fg(Color::LightBlue),
            ));
            continue;
        }
        let heading_level = trimmed
            .chars()
            .take_while(|character| *character == '#')
            .count();
        if heading_level > 0 && trimmed.as_bytes().get(heading_level) == Some(&b' ') {
            let title = trimmed[heading_level + 1..].trim();
            let color = if heading_level == 1 {
                theme_cyan()
            } else {
                theme_assistant()
            };
            lines.push(Line::styled(
                title.to_owned(),
                Style::default().fg(color).add_modifier(Modifier::BOLD),
            ));
        } else if matches!(trimmed, "---" | "***" | "___") {
            lines.push(Line::styled(
                "─".repeat(72),
                Style::default().fg(theme_border()),
            ));
        } else if let Some(item) = trimmed.strip_prefix("> ") {
            lines.push(Line::from(vec![
                Span::styled("│ ", Style::default().fg(theme_muted())),
                Span::styled(item.to_owned(), Style::default().fg(theme_foreground())),
            ]));
        } else if let Some(item) = trimmed
            .strip_prefix("- ")
            .or_else(|| trimmed.strip_prefix("* "))
        {
            lines.push(Line::from(vec![
                Span::styled("• ", Style::default().fg(theme_cyan())),
                Span::styled(item.to_owned(), Style::default().fg(theme_foreground())),
            ]));
        } else if is_markdown_table_separator(trimmed) {
            lines.push(Line::styled(
                "─".repeat(72),
                Style::default().fg(theme_border()),
            ));
        } else if trimmed.starts_with('|') && trimmed.ends_with('|') {
            let cells = trimmed
                .trim_matches('|')
                .split('|')
                .map(str::trim)
                .collect::<Vec<_>>()
                .join("  │  ");
            lines.push(Line::styled(cells, Style::default().fg(theme_foreground())));
        } else if trimmed.is_empty() {
            lines.push(Line::raw(""));
        } else {
            lines.push(Line::styled(
                raw.to_owned(),
                Style::default().fg(theme_foreground()),
            ));
        }
    }
    lines
}

pub(crate) fn is_markdown_table_separator(line: &str) -> bool {
    line.starts_with('|')
        && line.ends_with('|')
        && line
            .chars()
            .all(|character| matches!(character, '|' | '-' | ':' | ' '))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recognizes_table_separator() {
        assert!(is_markdown_table_separator("| --- | :---: |"));
        assert!(!is_markdown_table_separator("| value | value |"));
    }

    #[test]
    fn markdown_renderer_preserves_one_line_per_input_line() {
        let rendered = markdown_lines("# Title\n\n- item\n```rs\nlet x = 1;\n```");
        assert_eq!(rendered.len(), 6);
    }
}
