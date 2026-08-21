#!/usr/bin/env fish
#
# R2 — what does this terminal actually render?
#
# Every one of these is an assumption the UI currently rests on, and the mockups
# were never honest about attributes (docs/dev/model-rethink.md, and the traps
# section of docs/dev/state-of-the-port.md). `SGR 2` in particular: if Termux
# ignores it, every "dim" element in the app has been at full brightness all
# along, which changes what the quiet parts of the UI are doing.
#
# Run it in the terminal you care about — Termux on the phone is the one that
# matters. Read it with your eyes; there is nothing to assert.
#
#   usage:  fish tools/probe-attrs.fish

echo
echo "── 1 · attributes ──────────────────────────────────────"
echo "   Each row shows the attribute next to plain text. If the two halves look"
echo "   identical, this terminal ignores that attribute."
echo
printf '   dim        \e[2mSea Service Testimonial\e[0m   vs   Sea Service Testimonial\n'
printf '   bold       \e[1mSea Service Testimonial\e[0m   vs   Sea Service Testimonial\n'
printf '   reverse    \e[7mSea Service Testimonial\e[0m   vs   Sea Service Testimonial\n'
printf '   underline  \e[4mSea Service Testimonial\e[0m   vs   Sea Service Testimonial\n'
printf '   italic     \e[3mSea Service Testimonial\e[0m   vs   Sea Service Testimonial\n'
echo
echo "   Watch the underline especially: does it sit *below* the descenders, or"
echo "   cut through them? That is the thing that looked wrong before."
echo

echo "── 2 · the three surfaces ──────────────────────────────"
echo "   The theme maps tones onto these. ANSI 0 should be your background."
echo
printf '   ANSI 0  \e[40m        \e[0m  ANSI 7  \e[47m        \e[0m  ANSI 15 \e[107m        \e[0m\n'
printf '   band    \e[47;30m  14/14   ◀ back   ⏎ open file        \e[0m\n'
printf '   dim on band       \e[47;90m  dim text on the light band  \e[0m\n'
printf '   red on band       \e[47;31;1m  ! 3 expiring              \e[0m\n'
echo
echo "   If \"dim on band\" is unreadable, the on-band mapping needs revisiting."
echo

echo "── 3 · glyphs ──────────────────────────────────────────"
echo "   Anything that shows as a box or a blank is unusable on this device."
echo
printf '   prompt candidates   >   /   :   ⌕   🔍   ⌗\n'
printf '   markers             ▸   ·   —   ◀   ⏎   █\n'
printf '   status              !   ~   ✓   ✗   ⚠\n'
echo
echo "   The search-glyph idea (a glyph normally, > when the leader is pressed)"
echo "   depends on one of the prompt candidates rendering properly."
echo

echo "── 4 · size ────────────────────────────────────────────"
printf '   cols %s   lines %s\n' (tput cols) (tput lines)
echo "   (47x45 keyboard down, 47x24 keyboard up, on the measured device)"
echo
