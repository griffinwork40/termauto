/* termauto landing page — demo.js
 * Self-contained terminal animation. No globals; no external deps.
 * Sequence: TYPING → WAITING → PANEL → SELECTING → DONE → PAUSE → (loop)
 */
(function () {
  /* ---- Timing constants (ms) -------------------------------------------- */
  var CHAR_DELAY     = 80;   /* ms per character while typewriting            */
  var KEYPRESS_HOLD  = 400;  /* how long ^Space flash is visible              */
  var THINKING_HOLD  = 700;  /* how long "termauto: thinking…" is shown       */
  var STAGGER_HOLD   = 80;   /* delay between each candidate line appearing   */
  var SELECTING_HOLD = 500;  /* how long the "2" keypress indicator is shown  */
  var DONE_HOLD      = 2500; /* how long the accepted command is shown        */
  var PAUSE_HOLD     = 1500; /* blank pause before loop restart               */

  /* ---- Scripted content -------------------------------------------------- */
  var TYPED_TEXT   = '% git ';
  var CANDIDATES   = [
    '  1. git commit -am "fix: update deps"',
    '  2. git push origin main',
    '  3. git status'
  ];
  var LEGEND       = '  [1-3] accept  \u2022  [esc/^G] dismiss  \u2022  [e] edit top';
  var ACCEPTED_CMD = '% git push origin main';

  /* ---- Entry point ------------------------------------------------------- */
  document.addEventListener('DOMContentLoaded', function () {
    run();
  });

  function run() {
    var el = document.getElementById('demo-terminal');
    if (!el) return;
    typeText(el, TYPED_TEXT, 0);
  }

  /* ---- State: TYPING ----------------------------------------------------- */
  function typeText(el, text, index) {
    if (index > text.length) {
      /* Typing done — move to WAITING */
      afterTyping(el);
      return;
    }
    var typed = text.slice(0, index);
    el.innerHTML =
      '<span class="demo-prompt">% </span>' +
      '<span class="demo-input">' + escHtml(typed.slice(2)) + '</span>' +
      '<span class="demo-cursor"></span>';
    setTimeout(function () {
      typeText(el, text, index + 1);
    }, CHAR_DELAY);
  }

  /* ---- State: WAITING (^Space flash → thinking) -------------------------- */
  function afterTyping(el) {
    /* Show ^Space appended after typed text */
    el.innerHTML =
      '<span class="demo-prompt">% </span>' +
      '<span class="demo-input">git </span>' +
      '<span class="demo-keypress">^Space</span>';

    setTimeout(function () {
      /* Show thinking indicator */
      el.innerHTML = '<span class="demo-thinking">termauto: thinking\u2026</span>';

      setTimeout(function () {
        showPanel(el);
      }, THINKING_HOLD);
    }, KEYPRESS_HOLD);
  }

  /* ---- State: PANEL (staggered candidate lines) -------------------------- */
  function showPanel(el) {
    /* Start with just the prompt line */
    el.innerHTML = '<span class="demo-prompt">% git </span>';
    staggerCandidates(el, 0);
  }

  function staggerCandidates(el, index) {
    if (index < CANDIDATES.length) {
      /* Append next candidate line */
      el.innerHTML =
        '<span class="demo-prompt">% git </span>\n' +
        CANDIDATES.slice(0, index + 1).map(function (c) {
          return '<span class="demo-candidate">' + escHtml(c) + '</span>';
        }).join('\n');

      setTimeout(function () {
        staggerCandidates(el, index + 1);
      }, STAGGER_HOLD);
    } else {
      /* All candidates shown — append legend */
      el.innerHTML =
        '<span class="demo-prompt">% git </span>\n' +
        CANDIDATES.map(function (c) {
          return '<span class="demo-candidate">' + escHtml(c) + '</span>';
        }).join('\n') +
        '\n<span class="demo-legend">' + escHtml(LEGEND) + '</span>';

      setTimeout(function () {
        showSelecting(el);
      }, STAGGER_HOLD * 2);
    }
  }

  /* ---- State: SELECTING (keypress "2" indicator) ------------------------- */
  function showSelecting(el) {
    el.innerHTML =
      '<span class="demo-prompt">% git </span>' +
      '<span class="demo-keypress">2</span>\n' +
      CANDIDATES.map(function (c) {
        return '<span class="demo-candidate">' + escHtml(c) + '</span>';
      }).join('\n') +
      '\n<span class="demo-legend">' + escHtml(LEGEND) + '</span>';

    setTimeout(function () {
      showDone(el);
    }, SELECTING_HOLD);
  }

  /* ---- State: DONE (accepted command at full brightness) ----------------- */
  function showDone(el) {
    el.innerHTML = '<span class="demo-accepted">' + escHtml(ACCEPTED_CMD) + '</span>';

    setTimeout(function () {
      showPause(el);
    }, DONE_HOLD);
  }

  /* ---- State: PAUSE (blank, then loop) ----------------------------------- */
  function showPause(el) {
    el.innerHTML = '';

    setTimeout(function () {
      run();
    }, PAUSE_HOLD);
  }

  /* ---- Utility ----------------------------------------------------------- */
  function escHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
})();
