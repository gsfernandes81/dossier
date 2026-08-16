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

//! Headless measurement — the part of the spike that runs without a terminal.
//!
//! Two audiences. On CI (`--bench --assert-budget`) this is the synthetic perf
//! gate from REWRITE.md §9: it fails the build if a keystroke costs more than a
//! frame. On the phone it is the number that can be captured without fighting
//! a TUI in a screenshot — `ds-spike --bench` prints a table and exits, so the
//! measurement can be pasted straight into the protocol doc.
//!
//! It renders through ratatui's `TestBackend` (an in-memory buffer), so what it
//! measures is *layout plus row building* with terminal I/O excluded. That is
//! deliberate: the write to the tty is bounded by the terminal, not by us, and
//! the interactive run's own frame stats (F4) cover the end-to-end number.

use std::time::Instant;

use ratatui::backend::TestBackend;
use ratatui::Terminal;

use crate::app::App;
use crate::data::synth;
use crate::ui;

/// Terminal shapes worth measuring: portrait Termux, a classic 80×24, and a
/// wide desktop where the detail split is live.
const SHAPES: &[(&str, u16, u16)] =
    &[("phone portrait", 45, 28), ("classic", 80, 24), ("desktop split", 120, 40)];

/// Queries typed one character at a time — the keystroke→frame path, including
/// the filter over the whole store.
const TYPING: &str = "passport";

/// Run the headless benchmark. Returns the worst keystroke→frame in microseconds.
///
/// `docs` is the store size (1,000 for the spike's headline number); `rounds` is
/// how many frames to time per shape.
pub fn run(docs: usize, rounds: usize, assert_budget: bool) -> Result<(), String> {
    let build_start = Instant::now();
    let store = synth(docs);
    let build = build_start.elapsed();
    println!(
        "ds-spike bench — {} synthetic docs built in {:.2}ms",
        store.len(),
        build.as_secs_f64() * 1000.0
    );
    println!(
        "{:<16} {:>7} {:>10} {:>10} {:>10} {:>12}",
        "shape", "size", "median", "p95", "max", "worst key→frame"
    );

    let mut worst_key_us = 0u64;
    for &(label, width, height) in SHAPES {
        let mut app = App::new(synth(docs));
        let mut terminal =
            Terminal::new(TestBackend::new(width, height)).map_err(|e| e.to_string())?;

        // Steady-state frames: redraw the same view repeatedly, moving the
        // cursor so the virtualized window actually slides.
        for i in 0..rounds {
            app.selected = (i * 7) % app.filtered.len().max(1);
            let start = Instant::now();
            terminal.draw(|frame| ui::draw(frame, &mut app)).map_err(|e| e.to_string())?;
            app.frames.push(start.elapsed(), None);
        }

        // Typing: each character re-filters 1,000 rows and repaints. This is
        // the number the 16 ms budget is about.
        let mut key_worst = 0u64;
        for round in 0..rounds.max(1) {
            app.query.clear();
            for ch in TYPING.chars() {
                let start = Instant::now();
                app.query.push(ch);
                app.filtered = crate::data::filter(&app.docs, &app.query);
                app.selected = 0;
                terminal.draw(|frame| ui::draw(frame, &mut app)).map_err(|e| e.to_string())?;
                let us = start.elapsed().as_micros() as u64;
                // Skip the first round: it pays for the terminal's first diff.
                if round > 0 {
                    key_worst = key_worst.max(us);
                }
            }
        }
        app.query.clear();
        worst_key_us = worst_key_us.max(key_worst);

        let (_, median, p95, max) = app.frames.summary();
        println!(
            "{label:<16} {:>7} {:>9.2}ms {:>9.2}ms {:>9.2}ms {:>11.2}ms",
            format!("{width}x{height}"),
            median as f64 / 1000.0,
            p95 as f64 / 1000.0,
            max as f64 / 1000.0,
            key_worst as f64 / 1000.0,
        );
    }

    println!(
        "worst keystroke→frame across shapes: {:.2}ms (budget: 16ms target, 33ms acceptable)",
        worst_key_us as f64 / 1000.0
    );

    if assert_budget && worst_key_us > 33_000 {
        return Err(format!(
            "keystroke→frame {:.2}ms exceeds the 33ms acceptable ceiling (REWRITE.md §9)",
            worst_key_us as f64 / 1000.0
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    /// The synthetic perf gate itself must pass in CI, on the slowest runner —
    /// this is the REWRITE.md §9 "keystroke → frame" line, asserted rather than
    /// merely printed. A generous margin (33 ms) keeps it a real signal instead
    /// of a flake generator.
    #[test]
    fn keystroke_to_frame_stays_within_budget() {
        super::run(1000, 20, true).expect("keystroke→frame budget");
    }
}
