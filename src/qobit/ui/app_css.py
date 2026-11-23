"""The app's stylesheet, in two parts that ``QobitApp.CSS`` joins.

``BASE_CSS`` is the look itself and names no qobit widget: the screen inset,
scrollbars, tabs, the footer, list and grid-card selection, status lines and
the transparent-background overrides. ``QOBIT_CSS`` is what only qobit's own
widgets need (the tab views' height, the wordmark's overlay layer, the Mixes
card and the playing row under transparency).

Order between the two doesn't matter: nothing in QOBIT_CSS shares a selector
and a property with BASE_CSS, and the transparent-mode rules that must win do
so by specificity (an extra type selector), not by coming later.
"""

BASE_CSS = """
    Screen {
        layout: vertical;
        /* The whole app's horizontal inset lives here, not on individual
           children's margin. Textual's vertical-layout arrangement
           subtracts a margined child's *own* left+right margin from the
           width basis it uses to resolve `1fr` for every sibling in the
           same layout — so giving ContentSwitcher's content views and
           TransportBar their own independent horizontal margins made them
           drift out of alignment with each other (and with Tabs/Footer,
           which had no margin and so weren't inset at all). Padding on
           the shared Screen ancestor has no such cross-sibling effect, and
           it now insets Tabs/Footer identically to everything else.
        */
        padding: 0 1;
    }
    /* Modals (the `i`/`g` menus, the command palette):
       - no padding: they're Screens too, and Textual draws the page behind a
         modal inside the modal's padding, so it jumped one column right;
       - no tint: Kitty images are placeholder cells whose text colour encodes
         the image id. Tinting the page behind a modal changed those colours,
         and every image vanished while the menu was open. */
    ModalScreen { padding: 0; background: transparent; }
    /* Unified scrollbar design across every scrollable widget / page: a
       thumb on no track (a $surface track was a stripe of background that
       stayed with the theme background off). */
    * {
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
        scrollbar-background: $background 0%;
        scrollbar-background-hover: $background 0%;
        scrollbar-background-active: $background 0%;
        scrollbar-color: $surface-lighten-2;
        scrollbar-color-hover: $surface-lighten-2;
        scrollbar-color-active: $accent;
    }
    Tabs {
        dock: top;
    }
    ContentSwitcher {
        height: 1fr;
    }
    /* Lists sit on their panel. Textual's ListView paints $surface, a shade
       off the background: with an opaque background every list showed as a
       lighter box, larger than its rounded border. (Popups that want a
       surface, like the `i` menu, set it explicitly and win on specificity.) */
    ListView { background: transparent; }
    /* Backgrounds: page sections paint none (panels are set apart by their
       round borders), so the theme background, or the terminal's with it
       off, shows through everywhere and both modes look alike. The only
       fills are state (the selection, the now-playing row) and floating
       layers that must stay readable over the page (the i/g menu, the
       command palette). Textual's footer and Input bring their own fills:
       cleared here. */
    Footer, Footer > *, Input { background: transparent; }
    /* With no footer bar, the footer's text sits on the page: use colours
       that read there. Themes colour it for their own bar (solarized-light:
       near-white on blue, so with the bar gone it was invisible). Keys take
       the readout colour (amber in cyberdeck), descriptions the foreground. */
    FooterKey .footer-key--key {
        color: $qobit-readout;
        background: transparent;
    }
    FooterKey .footer-key--description {
        color: $foreground;
        background: transparent;
    }
    /* Tab labels: Textual fades the inactive ones to $foreground 50% (dim
       too in ANSI themes), 1.6:1 in some themes. Inactive: qobit's readable
       muted colour; active: the foreground, bold. With the bar focused,
       the active tab takes the selection pair, like a focused list's row
       (Textual's block-cursor pair was pink on cyan text, 2.7:1, in
       cyberdeck; the selection pair is measured per theme). */
    Tabs Tab { color: $qobit-muted; text-style: none; }
    Tabs Tab:hover, Tabs Tab.-active { color: $foreground; }
    Tabs Tab.-active { text-style: bold; }
    Tabs:focus Tab.-active {
        color: $qobit-select-text;
        background: $qobit-select;
    }

    /* List highlight. Textual 8 marks the row with `-highlight` (it was
       `--highlight` in older Textual; a rule on the old name silently
       stopped matching). Focused list: the selection, every label in the
       selection's text colour (themes.qobit_variable_defaults measures the
       pair per theme; the row drops its own span colours while highlighted,
       see _now_playing.plain_if_highlighted). Unfocused list: a faint tint
       and the row's normal colours, so the Search tab's three lists don't
       all look selected. Labels go transparent so the row's fill shows
       behind the text under Screen.-transparent too (same fix as .grid-card). */
    ListView > ListItem.-highlight {
        background: $qobit-select-dim;
        text-style: none;
    }
    ListView:focus > ListItem.-highlight {
        background: $qobit-select;
        text-style: none;
    }
    ListView:focus > ListItem.-highlight Label {
        color: $qobit-select-text;
    }
    ListItem.-highlight > Label {
        background: transparent;
    }
    /* Empty and failed lists/grids (_utils.status_line): one look everywhere. */
    .status-line {
        width: 1fr;
        padding: 0 1;
        color: $qobit-muted;
        text-style: italic;
    }
    .status-line.-error { color: $error; text-style: none; }
    /* Row lines never wrap: a long album name wrapped into the next row's
       space (or onto a stray third line). Cut it with an ellipsis instead. */
    .row-title, .row-meta {
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    /* Active-tab underline in the accent colour too (Textual draws it with
       $block-cursor-background, i.e. the theme's primary). */
    Tabs Underline > .underline--bar {
        color: $qobit-accent-text;
    }
    /* The footer scrolls horizontally when its key hints don't fit (narrow
       terminals), and its scrollbar then covers the hints. Hide it: the
       hints that fit stay visible. */
    Footer {
        scrollbar-size-horizontal: 0;
    }
    /* Grid card selection — one style for every library grid tile
       (AlbumCard / ArtistCard / PlaylistCard all carry .grid-card), matching
       a highlighted list row: accent tint + accent text, primary line bold.
       Lives here because App.CSS outranks every widget's DEFAULT_CSS.

       The tint must cover the text block, so the card's children are made
       see-through when selected. Under transparency, Screen.-transparent *
       below paints *every* widget ansi_default — including .card-info and
       its Labels, which then covered the card's tint everywhere except the
       1-col gap beside the image (the long-standing "selection looks wrong
       on Ghostty" issue: earlier fixes restored the *card's* background,
       which was correct but hidden). These selectors carry three classes,
       out-ranking that rule's one class + one type in both modes.

       No outline: a 5-row tile's text fills .card-info, and child Labels
       paint over the parent's outline cells, so it only ever showed on cards
       with spare rows (Artists) — the visible inconsistency. */
    .grid-card.-selected {
        background: $qobit-select;
    }
    .grid-card.-selected .card-info,
    .grid-card.-selected .card-info > Label,
    .grid-card.-selected TGPImage {
        background: transparent;
    }
    .grid-card.-selected .card-primary {
        color: $qobit-select-text;
        text-style: bold;
    }
    .grid-card.-selected .card-secondary,
    .grid-card.-selected TGPImage {
        color: $qobit-select-text;
    }
    /* The selection wins over a row's own state fill (the now-playing tint)
       in transparent mode too. The -now rule (QOBIT_CSS) has the same specificity
       as the plain highlight rules and comes later, so on the playing row
       the selection's background vanished while its text colour stayed:
       black text on a dark terminal in ansi-dark. These carry one more type
       selector than any row-state rule, whatever their order. */
    Screen.-transparent ListView > ListItem.-highlight {
        background: $qobit-select-dim;
    }
    Screen.-transparent ListView:focus > ListItem.-highlight {
        background: $qobit-select;
    }
    Screen.-transparent,
    Screen.-transparent * {
        background: ansi_default;
    }
"""

QOBIT_CSS = """
    /* Overlay: the tab bar's wordmark (nav.Wordmark) shares its row. */
    Screen {
        layers: default overlay;
    }
    SearchView, PlaylistsView, TracksView, ArtistsView, AlbumsView, MixesView, QueueView {
        height: 1fr;
        margin-bottom: 1;
    }
    /* Selection/status backgrounds that must survive transparency mode.
       Textual gives this App.CSS stylesheet an unconditional priority layer
       over *every* widget's own DEFAULT_CSS — a widget's rule can never
       out-rank an App.CSS rule by being more specific, no matter how it's
       written (confirmed empirically, not assumed). So the only place an
       override can actually win against Screen.-transparent * below is
       here, scoped under it so it only applies in transparent mode (the
       widgets' own rules already supply the same values the rest of the
       time). These are UI *state* — selection, now-playing, a
       modal's legibility — not page background, and losing them silently
       under transparency is worse than the terminal not showing through
       there specifically. */
    Screen.-transparent MixCard:focus {
        background: $qobit-select;
    }
    Screen.-transparent MixCard:focus Label {
        background: transparent;
    }
    Screen.-transparent TimelineRow.-now {
        background: $qobit-now;
    }
    /* ...and their labels see-through, or `Screen.-transparent *` paints them
       ansi_default and the tint shows only in the 1-col padding at each end
       (same fix as .grid-card.-selected). */
    Screen.-transparent TimelineRow.-now Label {
        background: transparent;
    }
"""
