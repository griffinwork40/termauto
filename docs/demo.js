/* termauto landing page — demo.js
 * Self-contained terminal animation. No globals, no external deps.
 *
 * First paint is the fully-populated candidate panel, so the hero is never an
 * empty box. From there it demonstrates the pick, the commit, then loops the
 * full interaction:
 *
 *   PANEL(rest) -> SELECTING -> DONE -> PAUSE -> TYPING -> WAITING
 *               -> THINKING -> PANEL(reveal) -> SELECTING -> ...
 *
 * Panel format mirrors shell/termauto.zsh `_termauto_render_panel` verbatim.
 */
(function () {
  /* ---- Timing (ms) ------------------------------------------------------ */
  var CHAR_DELAY     = 85;    /* per character while typing                  */
  var KEYCAP_HOLD    = 480;   /* ^Space keycap visible                       */
  var THINKING_HOLD  = 720;   /* "termauto: thinking…" visible               */
  var STAGGER        = 90;    /* delay between candidate lines on reveal     */
  var INITIAL_HOLD   = 1300;  /* resting panel before first pick             */
  var SELECTING_HOLD = 680;   /* highlighted selection visible               */
  var DONE_HOLD      = 2200;  /* accepted command sits at the prompt         */
  var PAUSE_HOLD     = 900;   /* blank beat before the cycle restarts        */

  /* ---- Scripted content ------------------------------------------------- */
  var INPUT      = 'git ';
  var SEL        = 1; /* zero-based index of the candidate that gets picked  */
  var CANDIDATES = [
    { n: 1, cmd: 'git commit -am "fix: update deps"' },
    { n: 2, cmd: 'git push origin main' },
    { n: 3, cmd: 'git status' }
  ];
  var LEGEND       = '[1-3] accept  \u2022  [esc/^G] dismiss  \u2022  [e] edit top';
  var ACCEPTED_CMD = 'git ' + CANDIDATES[SEL].cmd.replace(/^git /, ''); /* git push origin main */

  var el;

  document.addEventListener('DOMContentLoaded', function () {
    el = document.getElementById('demo-terminal');
    if (!el) return;
    /* First paint: the resting panel (richest, most informative frame). */
    el.innerHTML = panelHtml(CANDIDATES.length, -1);
    setTimeout(selecting, INITIAL_HOLD);
  });

  /* ---- Panel builder ---------------------------------------------------- */
  /* count = how many candidate rows to show; selIndex = row to highlight    */
  function panelHtml(count, selIndex) {
    var rows = CANDIDATES.slice(0, count).map(function (c, i) {
      var cls = 'demo-candidate' + (i === selIndex ? ' sel' : '');
      return '<span class="' + cls + '">  <span class="num">' + c.n + '.</span> ' +
             escHtml(c.cmd) + '</span>';
    });
    var body = '<span class="demo-prompt">% </span><span class="demo-input">' +
               escHtml(INPUT) + '</span>';
    if (rows.length) body += '\n' + rows.join('\n');
    if (count >= CANDIDATES.length) {
      body += '\n  <span class="demo-legend">' + escHtml(LEGEND) + '</span>';
    }
    return body;
  }

  /* ---- SELECTING: highlight the picked row, show the keycap ------------- */
  function selecting() {
    el.innerHTML =
      '<span class="demo-prompt">% </span><span class="demo-input">' + escHtml(INPUT) +
      '</span><span class="demo-keycap">2</span>\n' +
      CANDIDATES.map(function (c, i) {
        var cls = 'demo-candidate' + (i === SEL ? ' sel' : '');
        return '<span class="' + cls + '">  <span class="num">' + c.n + '.</span> ' +
               escHtml(c.cmd) + '</span>';
      }).join('\n') +
      '\n  <span class="demo-legend">' + escHtml(LEGEND) + '</span>';
    setTimeout(done, SELECTING_HOLD);
  }

  /* ---- DONE: accepted command sits at the prompt, ready to run ---------- */
  function done() {
    el.innerHTML =
      '<span class="demo-prompt">% </span>' +
      '<span class="demo-accepted">' + escHtml(ACCEPTED_CMD) + '</span>' +
      '<span class="demo-cursor"></span>';
    setTimeout(pause, DONE_HOLD);
  }

  /* ---- PAUSE: brief blank prompt before retyping ------------------------ */
  function pause() {
    el.innerHTML = '<span class="demo-prompt">% </span><span class="demo-cursor"></span>';
    setTimeout(function () { typeChar(0); }, PAUSE_HOLD);
  }

  /* ---- TYPING: typewriter the partial command --------------------------- */
  function typeChar(i) {
    if (i > INPUT.length) { waiting(); return; }
    el.innerHTML =
      '<span class="demo-prompt">% </span>' +
      '<span class="demo-input">' + escHtml(INPUT.slice(0, i)) + '</span>' +
      '<span class="demo-cursor"></span>';
    setTimeout(function () { typeChar(i + 1); }, CHAR_DELAY);
  }

  /* ---- WAITING: ^Space keycap, then the thinking indicator -------------- */
  function waiting() {
    el.innerHTML =
      '<span class="demo-prompt">% </span>' +
      '<span class="demo-input">' + escHtml(INPUT) + '</span>' +
      '<span class="demo-keycap">^Space</span>';
    setTimeout(function () {
      el.innerHTML = '<span class="demo-thinking">termauto: thinking\u2026</span>';
      setTimeout(function () { reveal(0); }, THINKING_HOLD);
    }, KEYCAP_HOLD);
  }

  /* ---- PANEL reveal: candidates stagger in, then loop to selection ------ */
  function reveal(count) {
    el.innerHTML = panelHtml(count, -1);
    if (count < CANDIDATES.length) {
      setTimeout(function () { reveal(count + 1); }, STAGGER);
    } else {
      setTimeout(selecting, STAGGER * 3);
    }
  }

  /* ---- Utility ---------------------------------------------------------- */
  function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
})();
