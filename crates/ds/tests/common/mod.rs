// Copyright © 2026-present gsfernandes81
//
// This file is part of "dossier".
//
// dossier is free software: you can redistribute it and/or modify it under the
// terms of the GNU Affero General Public License as published by the Free Software
// Foundation, either version 3 of the License, or (at your option) any later version.
//
// dossier is distributed in the hope that it will be useful, but WITHOUT ANY
// WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
// PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License along with
// dossier. If not, see <https://www.gnu.org/licenses/>.

//! Helpers the integration tests share.

use ds::app::{update, Model, Msg, WriteState};
use ds::find;
use ds::theme::Theme;
use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::Terminal;

/// Returns `model` allowed to write, which a `Model` nobody has told about a
/// device is not.
pub fn writable(mut model: Model) -> Model {
    model.write = WriteState::Ready { device: "desk".into() };
    model
}

/// Types `text` a character at a time.
pub fn type_str(model: &mut Model, text: &str) {
    for c in text.chars() {
        update(model, Msg::Char(c));
    }
}

/// Opens the location picker on the selected document.
pub fn picking(model: &mut Model) {
    update(model, Msg::Enter);
    update(model, Msg::Char(' '));
    update(model, Msg::Char('l'));
}

/// Backspaces until the open edit's buffer is empty.
pub fn clear_buffer(model: &mut Model) {
    let typed = model.edit.as_ref().map_or(0, |edit| edit.buffer.chars().count());
    for _ in 0..typed {
        update(model, Msg::Backspace);
    }
}

/// Draws one frame of `model` and returns its cells.
pub fn render(model: &mut Model, cols: u16, rows: u16, theme: Theme) -> Buffer {
    let mut terminal = Terminal::new(TestBackend::new(cols, rows)).expect("test backend");
    terminal.draw(|frame| find::draw(frame, model, theme)).expect("draw");
    terminal.backend().buffer().clone()
}
