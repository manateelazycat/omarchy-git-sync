-- Git Sync draws its own themed outline; compositor focus borders are redundant.
hl.window_rule({
  match = { class = "^org.quickshell$", title = "^Git Sync( \\[[0-9a-f]+\\])?$" },
  border_size = 0,
})
