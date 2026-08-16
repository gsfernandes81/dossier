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

//! Startup self-timing — the R0.2 answer to the R0.1 `DS_TIMING` probe.
//!
//! The rewrite's budget is *time to usable* (REWRITE.md §9), so the spike has to
//! measure the same moment the Python baseline measured: the first frame that
//! shows document rows. This module owns the stopwatch and the one-line
//! breakdown; nothing here touches the terminal.
//!
//! Design decision: milestones are recorded as `Option<Duration>` filled in as
//! the run passes them, rather than as a stream of events. A startup has a fixed,
//! known shape — four stops — and a struct of four fields is honest about that.

use std::time::{Duration, Instant};

/// Startup milestones, in the order a run passes them.
///
/// Created on the first line of `main` so `data`/`init`/`paint` are measured
/// against the same origin the shell's `time` wrapper sees (minus exec+dyld,
/// which no in-process probe can observe — see [`exec_to_main`]).
pub struct Startup {
    origin: Instant,
    /// Building the synthetic store (R3's analog: folding the journal).
    pub data: Option<Duration>,
    /// Entering raw mode + the alternate screen (terminal handshake cost).
    pub init: Option<Duration>,
    /// Rendering *and flushing* the first frame — the "usable" moment.
    pub paint: Option<Duration>,
}

impl Startup {
    /// Start the clock. Call this as the first statement of `main`.
    pub fn begin() -> Self {
        Self { origin: Instant::now(), data: None, init: None, paint: None }
    }

    /// Record a milestone as *cumulative* time since `begin`.
    ///
    /// Cumulative, not per-stage: the per-stage numbers are derived when
    /// printing, so a missing milestone can never silently shift the others.
    pub fn mark(&mut self, slot: Slot) {
        let at = self.origin.elapsed();
        // rust: `match` on an enum is the state-machine tool here, and the
        // compiler checks it is exhaustive — adding a `Slot` variant without
        // handling it is a compile error, not a silent no-op at runtime.
        match slot {
            Slot::Data => self.data = Some(at),
            Slot::Init => self.init = Some(at),
            Slot::Paint => self.paint = Some(at),
        }
    }

    /// The one-line breakdown, in the shape of R0.1's `ds-timing:` line so the
    /// two are readable side by side.
    ///
    /// ```text
    /// ds-spike-timing: usable 7ms (data 2ms · term init 0ms · first paint 4ms) [exec→main ~10ms · rss 6.2MB · 1000 docs]
    /// ```
    pub fn report(&self, docs: usize) -> String {
        let ms = |d: Duration| d.as_secs_f64() * 1000.0;
        let usable = self.paint.unwrap_or_else(|| self.origin.elapsed());
        let data = self.data.unwrap_or_default();
        let init = self.init.unwrap_or(data);
        let paint = self.paint.unwrap_or(init);
        let exec = match exec_to_main() {
            Some(d) => format!("exec→main ~{:.0}ms", ms(d)),
            None => "exec→main n/a (use the shell `time` wrapper)".to_string(),
        };
        let rss = match rss_bytes() {
            Some(b) => format!("rss {:.1}MB", b as f64 / 1_048_576.0),
            None => "rss n/a".to_string(),
        };
        format!(
            "ds-spike-timing: usable {:.1}ms (data {:.1}ms · term init {:.1}ms · \
             first paint {:.1}ms) [{exec} · {rss} · {docs} docs]",
            ms(usable),
            ms(data),
            ms(init.saturating_sub(data)),
            ms(paint.saturating_sub(init)),
        )
    }
}

/// Which milestone [`Startup::mark`] is recording.
#[derive(Clone, Copy, Debug)]
pub enum Slot {
    /// Synthetic store built and sorted.
    Data,
    /// Terminal in raw mode, alternate screen entered, mouse capture on.
    Init,
    /// First frame drawn and flushed.
    Paint,
}

/// Time from `execve` to the first line of `main`, on Linux and Android.
///
/// This is the slice the R0.1 baseline could only see as "shell total minus
/// probe total" (~300–380 ms of `CPython` boot). A native binary should make it
/// nearly nothing, and this proves it *from inside the process* on the phone,
/// where wrapping runs in `time` is awkward.
///
/// Resolution is `USER_HZ` (10 ms), hence the `~` in the report: it is a
/// magnitude check ("is this 5 ms or 300 ms?"), not a precision instrument. The
/// `time` wrapper in the protocol stays the authoritative number.
#[cfg(any(target_os = "linux", target_os = "android"))]
pub fn exec_to_main() -> Option<Duration> {
    // /proc/self/stat field 22 is the process start time in clock ticks since
    // boot; /proc/uptime is seconds since boot. The difference is our age.
    // Field 2 (comm) can contain spaces and parentheses, so split *after* the
    // last ')' rather than trusting whitespace splitting from the start.
    let stat = std::fs::read_to_string("/proc/self/stat").ok()?;
    let tail = &stat[stat.rfind(") ")? + 2..];
    let starttime_ticks: f64 = tail.split_whitespace().nth(19)?.parse().ok()?;
    let uptime = std::fs::read_to_string("/proc/uptime").ok()?;
    let uptime_secs: f64 = uptime.split_whitespace().next()?.parse().ok()?;
    // USER_HZ is 100 on every Linux/Android target we ship to; reading the real
    // value needs libc, and one dependency for a ±10 ms display is a bad trade.
    let age = uptime_secs - starttime_ticks / 100.0;
    (age.is_finite() && age >= 0.0).then(|| Duration::from_secs_f64(age))
}

/// Non-Linux fallback: the shell `time` wrapper is the only source (Windows).
#[cfg(not(any(target_os = "linux", target_os = "android")))]
pub fn exec_to_main() -> Option<Duration> {
    None
}

/// Resident set size in bytes — the §9 budget line "binary RSS on phone < 30 MB".
#[cfg(any(target_os = "linux", target_os = "android"))]
pub fn rss_bytes() -> Option<u64> {
    // /proc/self/statm field 2 is resident pages. 4 KiB pages hold on every
    // target here (Android's 16 KiB-page devices would over-report by 4×, which
    // the protocol's cross-check against `ps` would catch immediately).
    let statm = std::fs::read_to_string("/proc/self/statm").ok()?;
    let pages: u64 = statm.split_whitespace().nth(1)?.parse().ok()?;
    Some(pages * 4096)
}

/// Non-Linux fallback: no cheap RSS source without a platform crate.
#[cfg(not(any(target_os = "linux", target_os = "android")))]
pub fn rss_bytes() -> Option<u64> {
    None
}

/// A rolling record of frame render times, for the "keystroke → frame" budget.
///
/// Kept as a plain `Vec` of microseconds: 1000 samples is nothing, and having
/// every sample means percentiles are exact rather than estimated.
///
/// The `_us` suffixes are units, not noise: a microsecond field that reads like
/// a millisecond field is how a budget check silently passes.
#[derive(Default)]
#[allow(clippy::struct_field_names)]
pub struct FrameStats {
    samples_us: Vec<u64>,
    /// Micros from the input event that caused a redraw to the end of that draw.
    pub last_key_to_frame_us: Option<u64>,
    /// Worst keystroke→frame seen this session — the number the budget cares about.
    pub worst_key_to_frame_us: u64,
}

impl FrameStats {
    /// Record one completed `Terminal::draw`.
    pub fn push(&mut self, draw: Duration, since_input: Option<Duration>) {
        // Cap the history so a long session cannot grow without bound.
        if self.samples_us.len() >= 4096 {
            self.samples_us.clear();
        }
        self.samples_us.push(draw.as_micros() as u64);
        if let Some(d) = since_input {
            let us = d.as_micros() as u64;
            self.last_key_to_frame_us = Some(us);
            self.worst_key_to_frame_us = self.worst_key_to_frame_us.max(us);
        }
    }

    /// `(count, median_us, p95_us, max_us)` over the recorded frames.
    pub fn summary(&self) -> (usize, u64, u64, u64) {
        if self.samples_us.is_empty() {
            return (0, 0, 0, 0);
        }
        let mut sorted = self.samples_us.clone();
        sorted.sort_unstable();
        let pick = |q: f64| sorted[((sorted.len() - 1) as f64 * q) as usize];
        (sorted.len(), pick(0.5), pick(0.95), sorted[sorted.len() - 1])
    }
}
