/* The jkctl web UI.
 *
 * One page, one event stream, one serial port somewhere behind the server.
 * Everything the backend learns -- what the bus is doing, a fresh reading, a
 * flash's progress -- arrives as an event and lands in the state here, so two
 * browsers watching the same bank stay in step with each other without either
 * of them polling.
 *
 * Which tab and which unit are in the address bar (`#2/cells`), so a reload
 * comes back where you were and a link to one battery's cells is a link
 * somebody can send.
 */

import { configure as configureApi, subscribe } from '/core/js/api.js';
import { DraftDialog, offerWriter, PageApply, resetDrafts } from '/core/js/drafts.js';
import { Bell, configure as configureNotify, useTabAlerts } from '/core/js/notify.js';
import {
  Brand,
  configure as configureShell,
  DeviceId,
  Glyph,
  Header,
  License,
  Pane,
  ThemeToggle,
  useFavicon,
  useServerLink,
  useTabs,
  useTheme,
  Watchers,
  Watching,
} from '/core/js/shell.js';
import { configure as configureTrace, useAutoTrace } from '/core/js/trace.js';
import {
  ago,
  Badge,
  Chev,
  Empty,
  LiveToggle,
  OfflineNotice,
  Toasts,
  usePopover,
  useSteadyBusy,
  useToasts,
} from '/core/js/ui.js';
import { html, render, useEffect, useRef, useState } from '/core/vendor/preact-htm.module.js';
import * as api from './api.js';
import { Bank, BoardPicker, idOf, madeOf } from './bank.js';
import { Dashboard } from './dashboard.js';
import { FirmwareTab } from './firmware.js';
import { HistoryTab } from './history.js';
import { SETTINGS } from './panels.js';
import { PORT_KEYS, PortsTab, PROTOCOLS } from './ports.js';
import { RegistersTab } from './registers.js';
import { CellsTab, SettingsTab, SWITCHES, switchKey } from './settings.js';
import { ToolsTab } from './tools.js';

/* The tabs, in the order somebody works down them: what the bank is doing,
 * then this unit, then what it is set to, then the two catalogues, then the
 * things you do to it. */
/* What this program calls itself, to the shared modules that keep a key or
 * write a sentence with the name in it. */
const NAME = 'jkctl';

configureApi({ name: NAME });
configureNotify({ name: NAME });
configureShell({ name: NAME });
configureTrace({ name: NAME });

/* --- the mark ------------------------------------------------------------
 *
 * A battery standing upright, half charged: the terminal on top, the case
 * around it, and the charge filling the lower half of the inside.  This is
 * what this program is about, and a battery that is neither full nor flat
 * is the state it is interesting in -- a full one and an empty one are both
 * a solid rectangle at sixteen pixels.
 *
 * Drawn once, here, and worn in both places a program's mark appears: in
 * front of the wordmark by `Glyph`, and in the browser's tab by
 * `useFavicon`.  There used to be one battery built out of borders in
 * app.css and a different one percent-encoded into index.html -- a case of
 * different proportions with the charge at a different level -- and nothing
 * but looking at the two of them could have said so.
 *
 * `currentColor` is the header's text there and `--tab-mark` in the tab;
 * see `useFavicon`.
 */
const MARK = {
  viewBox: '0 0 16 16',
  shapes: [
    /* the terminal */
    [
      'rect',
      { x: 6.1, y: 1.5, width: 3.8, height: 1.9, rx: 0.8, fill: 'currentColor', stroke: 'none' },
    ],
    /* the case */
    ['rect', { x: 3.7, y: 3.4, width: 8.6, height: 11.1, rx: 2 }],
    /* half of the inside, filled from the bottom */
    [
      'path',
      {
        d: 'M5.2 9.4h5.6v3.1a0.9 0.9 0 0 1-0.9 0.9H6.1a0.9 0.9 0 0 1-0.9-0.9z',
        fill: 'currentColor',
        stroke: 'none',
      },
    ],
  ],
};

const TABS = [
  ['bank', 'Bank'],
  ['dashboard', 'Dashboard'],
  ['settings', 'Settings'],
  ['cells', 'Cells'],
  ['ports', 'Ports'],
  ['history', 'History'],
  ['registers', 'Registers'],
  ['firmware', 'Firmware'],
  ['tools', 'Tools'],
];

/* How many live readings this tab keeps per unit.  At the default three-second
 * beat that is about an hour, which is long enough to see the effect of
 * something you changed and short enough to stay in a tab's memory. */
const MAX_SAMPLES = 1200;

/* And how many write marks.  A rule per write, and nobody writes a hundred
 * settings to one board in an hour -- but a page left open for a week on a
 * unit somebody is fiddling with should not grow without end either. */
const MAX_MARKS = 100;

/* --- the link pill ------------------------------------------------------- */

const LINK_WORDS = {
  released: 'port free',
  opening: 'opening',
  idle: 'port held',
  busy: 'busy',
  flashing: 'flashing',
  error: 'error',
};

/* The shape of the pill and of the menu under it are `core.css`'s -- the
 * same control alfenctl hangs off the same corner of the same header.  What
 * is this program's is the words: a serial port is held or free, where a
 * charger is connected or released.
 *
 * How it opens and closes is `usePopover`'s, which is what alfenctl's pill
 * has always used.  This one kept its own flag, so it closed only when the
 * pill itself was clicked again: a click on the page behind it left it
 * hanging over the tab you were trying to read, and Escape did nothing.
 * The same control in the same corner of the same header behaved one way in
 * one program and another way in the other.
 */
function LinkPill({ link, offline, onLink, onLive, onChoosePort, onWatchers, readOnly }) {
  const menu = usePopover();
  const state = link?.state || 'released';
  const word = LINK_WORDS[state] || state;
  const detail = link?.op || '';
  return html`<div class=${`anchor${menu.open ? ' open' : ''}`} ref=${menu.box}>
    <button
      class=${`pill ${offline ? 'gone' : state}`}
      type="button"
      ref=${menu.trigger}
      aria-expanded=${menu.open}
      aria-haspopup="dialog"
      onClick=${menu.toggle}
      title=${link?.error || 'What the serial port is doing'}
    >
      <span class="dot"></span>
      <span class="label">${offline ? 'server unreachable' : word}</span>
      ${!offline && detail ? html`<span class="pill-op">${detail}</span>` : null}
      ${!offline && link?.queued
        ? html`<span class="queued">${link.queued} waiting</span>`
        : null}
      <${Chev} down />
    </button>
    ${menu.open
      ? html`<div class="menu" role="dialog" aria-label="The serial port">
          <div class="row"><div class="k">Port</div><div class="v mono">${link?.port || '—'}</div></div>
          <div class="row"><div class="k">Baud</div><div class="v">${link?.baud || '—'}</div></div>
          <div class="row">
            <div class="k">Live updates</div>
            <div class="v">
              <label class="toggle">
                <input
                  type="checkbox"
                  checked=${!!link?.live}
                  onChange=${(e) => onLive(e.target.checked, link?.pollInterval)}
                />
                <span class="muted">every ${link?.pollInterval ?? 3}s</span>
              </label>
            </div>
          </div>
          <div class="wrap" style="margin-top:10px">
            <button class="btn" onClick=${() => onLink('connect')}>Hold the port</button>
            <button class="btn" onClick=${() => onLink('release')}>Release it</button>
            <!-- The port is named two rows above this, so this is where
                 somebody who has just read the wrong port's name looks for
                 the way to change it. -->
            <button
              class="btn"
              onClick=${() => {
                menu.close();
                onChoosePort();
              }}
            >
              Choose another…
            </button>
            <span class="spacer"></span>
            <!-- Who else is looking at this bus.  It belongs in this menu
                 because the port is the thing being shared: "someone is
                 holding it" and "who is holding it" are one question, and
                 the count is already in every link document.  The same
                 control sits in the same menu on the charger's page. -->
            <!-- The list opens over the menu and leaves it open: the menu is
                 where it was asked for, and closing the list goes back to it. -->
            <${Watching} clients=${link?.clients} onOpen=${onWatchers} />
          </div>
          <p class="muted" style="font-size:var(--fs-xs);margin:10px 0 0">
            Only one program can hold a serial port. While this page holds it, a terminal
            running <span class="mono">jkctl</span> cannot — it is given back
            ${link?.idleTimeout ?? 45}s after the last thing anybody asked for.
          </p>
          ${link?.activity?.length
            ? html`<div style="margin-top:12px">
                <div class="muted" style="font-size:var(--fs-xs);text-transform:uppercase">Recent</div>
                <ul class="list">
                  ${link.activity.slice(0, 6).map(
                    (item, i) => html`<li key=${i}>
                      ${item.ok === false ? html`<${Badge} tone="bad">failed<//>` : null}
                      <span>${item.op}</span>
                      <span style="flex:1"></span>
                      <span class="muted">${ago((Date.now() - item.at * 1000) / 1000)}</span>
                    </li>`,
                  )}
                </ul>
              </div>`
            : null}
          ${readOnly
            ? html`<p class="muted" style="font-size:var(--fs-xs);margin-top:10px">
                This server is read-only: nothing on the battery can be changed from here.
              </p>`
            : null}
        </div>`
      : null}
  </div>`;
}

/* --- the port picker ----------------------------------------------------- */

/* The last port a scan found boards on, in this browser.
 *
 * The configuration file can name the port, and then this page never asks.
 * Somebody who has not written one yet is asked every time, and the answer
 * is almost always the one that worked last time -- so the picker starts
 * on it, if it is still plugged in.  Browser storage can be missing or
 * refuse, in which case the picker simply starts where it used to. */
const LAST_PORT = 'jkctl.lastPort';

function lastPort() {
  try {
    return JSON.parse(window.localStorage.getItem(LAST_PORT) || 'null');
  } catch {
    return null;
  }
}

function rememberPort(doc) {
  try {
    window.localStorage.setItem(LAST_PORT, JSON.stringify({ port: doc.port, baud: doc.baud }));
  } catch {
    /* Nowhere to keep it; next time starts from the list. */
  }
}

function PortPicker({ onChoose, onCancel, busy, current, problem }) {
  const [ports, setPorts] = useState(null);
  const [chosen, setChosen] = useState(current?.port || '');
  const [baud, setBaud] = useState(current?.baud || 115200);
  const [error, setError] = useState('');

  const look = async () => {
    setError('');
    try {
      const doc = await api.ports();
      setPorts(doc.ports);
      const last = lastPort();
      const known = last && doc.ports.some((port) => port.device === last.port) ? last : null;
      setChosen(
        (was) => was || doc.current?.port || known?.port || doc.ports[0]?.device || '',
      );
      /* The speed the port that gets chosen will actually be opened at:
       * the one in use if there is one, then the one that worked last time
       * on the port remembered, otherwise what the configuration file says.
       * A bus running at 9600 should not be offered 115200. */
      const speed = doc.current?.baud || known?.baud || doc.defaults?.baud;
      if (speed) setBaud(speed);
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    look();
  }, []);

  return html`<div class="grid">
    <section class="card wide">
      <h2><span class="grow">Choose a serial port</span></h2>
      ${problem ? html`<div class="notice error">${problem}</div>` : null}
      <details class="help" open>
        <summary>Which of these is the RS485 adapter?</summary>
        <p>
          JK ships every board at 115200 8N1, and a four-position DIP switch on the board
          picks its address, 0 to 15. Choosing a port here scans all sixteen and shows what
          answers — nothing is written. If nothing answers, check that A and B are not
          swapped, then try <span class="mono">jkctl probe</span> in a terminal, which
          sweeps the baud rates and writes a report.
        </p>
      </details>
      ${error ? html`<div class="notice error">${error}</div>` : null}
      ${ports === null
        ? html`<${Empty}>Looking…<//>`
        : !ports.length
          ? html`<${Empty}>No serial ports on this machine. Plug the adapter in and press Look again.<//>`
          : html`<div>
              ${ports.map(
                (port) => html`<label class="row" key=${port.device} style="cursor:pointer">
                  <div class="k">
                    <input
                      type="radio"
                      name="port"
                      checked=${chosen === port.device}
                      onChange=${() => setChosen(port.device)}
                    />
                    <span class="mono" style="margin-left:8px">${port.device}</span>
                  </div>
                  <div class="v">
                    ${port.description || 'no description'}
                    ${port.likely ? html` <${Badge} tone="info">likely<//>` : null}
                    ${port.device === current?.port ? html` <${Badge}>in use now<//>` : null}
                  </div>
                </label>`,
              )}
            </div>`}
      <div class="card-foot">
        <label class="muted">
          baud
          <input
            class="num"
            type="number"
            style="width:6rem;margin-left:6px"
            value=${baud}
            onInput=${(e) => setBaud(Number(e.target.value))}
          />
        </label>
        <span style="flex:1"></span>
        ${onCancel ? html`<button class="btn" onClick=${onCancel}>Cancel</button>` : null}
        <button class="btn" onClick=${look}>Look again</button>
        <button
          class="btn primary"
          disabled=${!chosen || busy}
          onClick=${() => onChoose({ port: chosen, baud })}
        >
          Use it and scan
        </button>
      </div>
    </section>
  </div>`;
}

/* --- the app ------------------------------------------------------------- */

function App() {
  const theme = useTheme();
  useFavicon(MARK);
  const nav = useTabs(TABS);
  const server = useServerLink();
  const [state, setState] = useState(null);
  const [link, setLink] = useState(null);
  const [bank, setBank] = useState([]);
  const [busy, setBusy] = useState(false);
  const [jobs, setJobs] = useState({});
  const [picking, setPicking] = useState(false);
  /* Whether the port picker is up, and what the last attempt at one had to
   * say.  The picker is not only a first-run screen: picking the wrong
   * adapter out of a list of three is the ordinary way this goes, and the
   * way back has to be on the page rather than in a restart. */
  const [choosing, setChoosing] = useState(false);
  const [portProblem, setPortProblem] = useState('');
  const toast = useToasts();
  const alerts = useTabAlerts();

  // Per-unit documents, cleared when the unit changes.
  const [dashboard, setDashboard] = useState(null);
  // The newest reading per unit, from whichever came last: the live refresh
  // or the read that opened the tab.  Kept apart from `dashboard` because
  // only this half of that document changes on its own.
  const [readings, setReadings] = useState({});
  const [settings, setSettings] = useState(null);
  const [registers, setRegisters] = useState(null);
  const [ports, setPorts] = useState(null);
  const [doctorReport, setDoctorReport] = useState(null);
  const [library, setLibrary] = useState(null);
  const [history, setHistory] = useState(null);
  const [switches, setSwitches] = useState(null);
  const [logCodes, setLogCodes] = useState(null);
  const [libraryError, setLibraryError] = useState('');
  const [panelError, setPanelError] = useState('');
  /* What every unit on the bus has been doing, and when something was
   * written to it.  Both are per unit: a setting written to board 1 is not
   * a mark on board 3's chart, which is what one shared list of moments
   * made it.
   *
   * State rather than a ref.  A ref was enough while the only thing that
   * ever added to it was the live event -- which lands beside a `setBank`
   * that redraws the page anyway -- but the seed below arrives on its own,
   * and a chart that fills in only when something else happens to redraw
   * is a chart that appears empty. */
  const [samples, setSamples] = useState({});
  const [marks, setMarks] = useState({});
  /* Which of the browsers on `/api/clients` is this one.  Only the stream
   * can say: two tabs share an address and a user agent. */
  const [me, setMe] = useState(null);
  const [watching, setWatching] = useState(false);

  const units = link?.units || [];
  /* The hash carries the board as well as the tab -- `#3/settings` -- so a
   * link to one board's settings is a link somebody can send. */
  const unitId = (nav.prefix === null ? null : Number(nav.prefix)) ?? units[0]?.id ?? null;

  const tab = nav.tab;
  const offline = server.offline;
  const readOnly = !!state?.readOnly;
  const steady = useSteadyBusy(busy);

  /* A failure the board or the bus caused starts the serial trace, and one
   * that happens while it is running offers it -- from the notice itself.
   * See /core/js/trace.js; the switch for it is on the Tools tab. */
  /* The reply carries the link, recording state and all, and it is put up
   * at once rather than left for the next event: a card that goes on
   * saying "recording" after Stop is a card nobody believes. */
  const traceCall = async (action, extra) => {
    const doc = await api.trace(action, extra);
    setLink(doc.link);
    return doc;
  };
  const autoTrace = useAutoTrace({
    toast,
    recording: !!link?.trace?.on,
    automatic: !!link?.trace?.automatic,
    start: () => traceCall('start', { automatic: true }),
    stop: () => traceCall('stop').catch((err) => toast.error(`the trace: ${err.message}`)),
    download: () =>
      api.traceReport().catch((err) => toast.error(`the trace report: ${err.message}`)),
  });

  /* --- the event stream ------------------------------------------------- */
  useEffect(
    () =>
      subscribe('/api/events', {
        link: (doc) => {
          setLink(doc);
          server.found();
        },
        bank: (doc) => {
          setBank(doc.units || []);
          const now = Date.now() / 1000;
          setSamples((held) => {
            const next = { ...held };
            for (const row of doc.units || []) {
              if (!row.ok) continue;
              const list = [
                ...(next[row.id] || []),
                {
                  t: now,
                  power: row.power,
                  /* The board reports the power unsigned and the direction
                   * only here, so the chart needs both.  Left out until a
                   * chart drawn from the two of them turned every live
                   * sample into a charge. */
                  current: row.current,
                  soc: row.soc,
                  voltage: row.voltage,
                  temp: row.temperature,
                },
              ];
              next[row.id] = list.slice(-MAX_SAMPLES);
            }
            return next;
          });
        },
        hello: (doc) => setMe(doc.clientId),
        runtime: (doc) => setReadings((prev) => ({ ...prev, ...(doc.units || {}) })),
        units: () => refreshState(),
        job: (job) => {
          setJobs((prev) => ({ ...prev, [job.id]: job }));
          if (job.state === 'done') toast.ok(`${job.name}: done`);
          if (job.state === 'failed') toast.error(`${job.name}: ${job.error}`);
        },
        focus: () => {
          window.focus();
          flashTitle();
          alerts.notify();
          toast.info('jkctl ui was started again -- this is the tab it meant.');
        },
        onerror: () => server.lost(),
        onopen: () => {
          server.found();
          refreshState();
        },
      }),
    []
  );

  const refreshState = async () => {
    try {
      const doc = await api.state();
      setState(doc);
      setLink(doc.link);
      server.found();
    } catch {
      /* `onUnreachable` has already told `useServerLink`; a failure that
       * reached the server is the page's next read to worry about. */
    }
  };

  useEffect(() => {
    refreshState();
  }, []);

  /* The chart's own memory, from before this page opened.
   *
   * The server has been recording a sample per unit on every live sweep all
   * along, and serving them at `/api/samples`, and nothing ever asked: the
   * page kept its own copy from the events it happened to be there for, so
   * a reload -- or a second browser -- started at an empty chart and filled
   * in one pixel every three seconds.  This seeds from the server's record
   * and keeps whatever the live events have already added on top of it. */
  useEffect(() => {
    if (unitId === null || !link?.port) return undefined;
    let dropped = false;
    api
      .samples(unitId)
      .then((doc) => {
        if (dropped) return;
        setSamples((held) => {
          const seed = doc.samples || [];
          if (!seed.length) return held;
          const last = seed[seed.length - 1].t;
          const mine = (held[unitId] || []).filter((sample) => sample.t > last);
          return { ...held, [unitId]: [...seed, ...mine].slice(-MAX_SAMPLES) };
        });
      })
      .catch(() => {
        /* The chart is the page's own memory either way; it simply starts
         * where this look at the board started. */
      });
    return () => {
      dropped = true;
    };
  }, [unitId, link?.port]);

  /* Reading a unit clears everything the last one said: none of it is true
   * of the next board. */
  useEffect(() => {
    setDashboard(null);
    setSettings(null);
    setRegisters(null);
    setPorts(null);
    setDoctorReport(null);
    setLibrary(null);
    setHistory(null);
    setSwitches(null);
    /* And what was changed for it and not sent: an edit made for one
     * battery is not an edit for the next. */
    resetDrafts();
  }, [unitId]);

  const guard = async (what, fn, { quiet } = {}) => {
    setBusy(true);
    setPanelError('');
    try {
      return await fn();
    } catch (err) {
      if (!err.offline && !quiet && !autoTrace.failed(what, err)) {
        toast.error(`${what}: ${err.message}`);
      }
      setPanelError(err.message);
      throw err;
    } finally {
      setBusy(false);
    }
  };

  /* Read one unit, and file its reading where the live refresh files its
   * own.  Whichever of the two spoke last is the one drawn, which is what
   * makes the tab right whether or not live updates are on. */
  const readUnit = async () => {
    const doc = await api.dashboard(unitId);
    setDashboard(doc);
    setReadings((prev) => ({ ...prev, [String(unitId)]: doc.runtime }));
    return doc;
  };

  /* --- per-tab reads ---------------------------------------------------- */

  useEffect(() => {
    /* `=== null`, not `!unitId`: zero is an address.  A four-way DIP switch
     * with nothing on it is board 0, and a page that treated it as "no
     * board chosen" drew the header for it and then read nothing. */
    if (unitId === null) return;
    /* And not until a port is held.  Every read below is a question put to
     * a board on a bus; with no bus open the server can only answer "no
     * serial port has been chosen yet", and the page is showing the port
     * picker rather than the tab anyway.
     *
     * Waiting is also what makes the read happen at all.  A page reloaded
     * at `#1/dashboard` fires this effect on its first render, long before
     * anybody has picked a port; the read failed, and nothing ever woke it
     * again -- choosing the port and sweeping the bus changes neither the
     * tab nor the unit when the hash already names the board the sweep
     * finds, and this effect watches nothing else.  The dashboard then sat
     * at "Reading this unit..." for as long as the page was left open, with
     * the port held, the bank listed and the header naming the board.  The
     * port is therefore one of the things this effect is about. */
    if (!link?.port) return;
    if (tab === 'dashboard' && !dashboard) {
      guard('reading the unit', readUnit).catch(() => {});
    }
    if ((tab === 'settings' || tab === 'cells') && !settings) {
      guard('reading the configuration', async () =>
        setSettings((await api.settings(unitId)).settings),
      ).catch(() => {});
    }
    if (tab === 'settings' && !switches) {
      guard('reading the switches', async () =>
        setSwitches((await api.switches(unitId)).switches),
      ).catch(() => {});
    }
    if (tab === 'cells' && !dashboard) {
      guard('reading the unit', readUnit).catch(() => {});
    }
    if (tab === 'registers' && !registers) {
      guard('reading every table', async () =>
        setRegisters((await api.registers(unitId)).registers),
      ).catch(() => {});
    }
    if (tab === 'ports' && !ports) {
      guard('reading the ports', async () => setPorts(await api.protocols(unitId))).catch(() => {});
    }
  }, [tab, unitId, link?.port, dashboard, settings, registers, ports, switches]);

  /* The bank is read once a port is held, on whatever tab is open.
   *
   * It used to be the Bank tab's own read, which was fine while nothing else
   * wanted the list -- but the header names the board being looked at, and a
   * link straight to `#2/settings` therefore opened on a page that could not
   * say which of the boards on the bus it was about.  This is the read that
   * knows: it is the nameplates, it is what the picker beside the name is a
   * list of, and it happens once per port.
   *
   * Once per *port*.  It used to wake on every link state as well, which is
   * a loop with a motor in it: reading the bank moves the link from released
   * to opening to busy to idle, and each of those woke the read again.  On a
   * bus where nothing answers the list never fills, so nothing ever stopped
   * it -- one wrong port in the picker queued sweeps faster than a serial
   * bus can run them, and every one of them that ran out of patience arrived
   * as its own notification. */
  const bankPort = useRef(null);
  const loadBank = (port) => {
    bankPort.current = port;
    setBank([]);
    guard('reading the bank', async () => setBank((await api.bank()).units)).catch(() => {});
  };
  useEffect(() => {
    if (!link?.port) {
      bankPort.current = null;
      return;
    }
    /* Not while the picker is up.  On a bus where nothing answered, reading
     * the bank sweeps all sixteen addresses again -- seconds of a serial
     * port held for a list the user is in the middle of replacing. */
    if (choosing) return;
    if (bankPort.current !== link.port) loadBank(link.port);
  }, [link?.port, choosing]);

  /* --- actions ---------------------------------------------------------- */

  const openUnit = (id) => nav.show('dashboard', id);

  /* A moment something was written, for the chart to draw a rule on, with
   * the words that go in its tooltip.  Per unit, and only for writes: a
   * read is the page looking, and marking those would put a rule every
   * three seconds. */
  const remember = (what) =>
    setMarks((held) => ({
      ...held,
      [unitId]: [...(held[unitId] || []), { at: Date.now() / 1000, what }].slice(-MAX_MARKS),
    }));

  const reread = () => {
    setSettings(null);
    setRegisters(null);
    setDashboard(null);
    setPorts(null);
  };
  /* A write that fails is read back too: a plan is several writes, and
   * the ones before the refusal went in -- and a refusal the board makes
   * whatever it is sent comes back on the register, which the page then
   * shows in place of its editor. */
  const writeSettings = async (edits, { table, dryRun, preset } = {}) => {
    let doc;
    try {
      doc = await guard('writing', () =>
        api.writeSettings({ id: unitId, changes: edits, dryRun: !!dryRun, table, preset }),
      );
    } catch (err) {
      if (!dryRun) reread();
      throw err;
    }
    if (!dryRun && doc.changes.length) {
      remember(`wrote ${doc.changes.length} setting(s)`);
      toast.ok(`Wrote ${doc.changes.length} setting(s) to BMS ${unitId}`);
      reread();
    }
    return doc;
  };

  /* --- how each kind of edit is sent -------------------------------------
   *
   * Offered here rather than by the tabs, because only the tab being looked
   * at is built and an edit made on another one is still in the draft: the
   * header's Apply has to be able to send it from wherever it is.  Each
   * shows its plan before it writes (see /core/js/drafts.js). */

  /* Settings live in two tables -- the settings table and the few writable
   * registers of the device-info one, which the Ports tab and the register
   * browser edit -- and the server plans a table at a time.  The documents
   * already read say which table a name is in. */
  const deviceInfo = new Set(
    [...(ports?.outputs || []), ...(registers || [])]
      .filter((reg) => reg.table === '03')
      .map((reg) => reg.name),
  );
  const byTable = (edits) => {
    const parts = { '01': {}, '03': {} };
    for (const [key, value] of Object.entries(edits)) {
      parts[deviceInfo.has(key.replace(/\[\d+\]$/, '')) ? '03' : '01'][key] = value;
    }
    return Object.entries(parts).filter(([, part]) => Object.keys(part).length);
  };
  offerWriter(SETTINGS, {
    title: 'Settings',
    busy: steady,
    disabled: readOnly,
    plan: async (edits) => {
      const changes = [];
      for (const [table, part] of byTable(edits)) {
        const doc = await writeSettings(part, { dryRun: true, table: table === '01' ? undefined : table });
        changes.push(...doc.changes);
      }
      return changes;
    },
    write: async (edits) => {
      for (const [table, part] of byTable(edits)) {
        await writeSettings(part, { table: table === '01' ? undefined : table });
      }
    },
  });

  /* The multiplexed switches: one bit at a time, each a fresh read of the
   * word and a write of it back, so a second bit does not carry a stale
   * copy of the first. */
  const switchChanges = (edits) =>
    (switches || [])
      .filter((row) => switchKey(row.bit) in edits)
      .map((row) => ({
        name: switchKey(row.bit),
        label: row.name,
        oldText: row.on ? 'on' : 'off',
        newText: edits[switchKey(row.bit)] ? 'on' : 'off',
      }))
      .filter((change) => change.oldText !== change.newText);
  offerWriter(SWITCHES, {
    title: 'Multiplexed switches',
    busy: steady,
    disabled: readOnly,
    plan: async (edits) => switchChanges(edits),
    write: async (edits) => {
      try {
        for (const row of switches || []) {
          const key = switchKey(row.bit);
          if (!(key in edits) || edits[key] === row.on) continue;
          await guard('setting a switch', () =>
            api.writeSwitch({ id: unitId, bit: row.bit, on: edits[key] }),
          );
          remember(`${row.name} ${edits[key] ? 'on' : 'off'}`);
        }
        toast.ok(`Wrote the switches to BMS ${unitId}`);
      } finally {
        setSwitches((await api.switches(unitId)).switches);
      }
    },
  });

  /* The port protocols: a selector each, set through a call of its own. */
  const protocolChanges = (edits) =>
    (ports?.ports || [])
      .filter((port) => PORT_KEYS[port.port] in edits)
      .map((port) => {
        const word = PORT_KEYS[port.port];
        const kind = ports.selectors.find((sel) => sel.name === word)?.kind || 'uart';
        const wanted = (ports.lists[kind] || []).find((p) => p.id === edits[word]);
        return {
          name: word,
          label: port.port,
          oldText: port.protocol,
          newText: wanted?.name ?? String(edits[word]),
        };
      });
  offerWriter(PROTOCOLS, {
    title: 'Port protocols',
    busy: steady,
    disabled: readOnly,
    plan: async (edits) => protocolChanges(edits),
    write: async (edits) => {
      try {
        for (const [selector, number] of Object.entries(edits)) {
          await guard('setting the protocol', () =>
            api.writeProtocol({ id: unitId, selector, number }),
          );
        }
        remember('set the port protocols');
        toast.ok('Set. The unit may need a restart before it speaks a new protocol.');
      } finally {
        setPorts(null);
      }
    },
  });

  const actions = {
    onSync: async () => {
      await guard('syncing the clock', () => api.runAction({ id: unitId, action: 'time-sync' }));
      toast.ok('The unit’s clock was set from this host');
      await readUnit();
    },
    onDoctor: async () => {
      setDoctorReport(await guard('checking', () => api.doctor(unitId)));
    },
  };

  /* What the dashboard and the cells tab draw: the document the tab was
   * opened with, with the newest reading in place of the one it arrived
   * with.  Everything else in it -- the identity, the setpoints, the
   * switches -- only changes when something writes it, and is re-read then
   * rather than on every beat. */
  const reading = readings[String(unitId)];
  const unitDoc = dashboard && reading ? { ...dashboard, runtime: reading } : dashboard;

  /* The board the header is naming.  The scan is where its nameplate
   * normally comes from; a dashboard read that landed first carries the same
   * four facts, so the header does not wait for the one to say what the
   * other already knows. */
  const unit = units.find((u) => u.id === unitId) || fromIdentity(unitId, dashboard?.identity);

  /* One tab's content, by name.
   *
   * A table rather than the chain of nested ternaries this was: nine of
   * them, twelve levels deep at the bottom, in which the guard clauses at
   * the top -- no port, no unit -- were indistinguishable from the tabs
   * themselves, and adding a tab meant adding a level.
   *
   * Only the tab being looked at is built.  That is not what `Pane` is for
   * -- every pane stays mounted so a tab keeps what it read -- but these
   * panels read on mount through `App`'s own effects, and building all
   * nine at once would put nine reads on one serial bus.
   */
  const needsUnit = (draw) => () =>
    unitId === null
      ? html`<${Empty}>No unit selected. The Bank tab lists what answered.<//>`
      : draw();

  const panels = {
    bank: () => html`<${Bank}
      rows=${bank}
      selected=${unitId}
      busy=${steady}
      port=${link?.port}
      onOpen=${openUnit}
      onChoosePort=${() => setChoosing(true)}
      onRescan=${async () => {
        await guard('scanning', () => api.rescan());
        setBank((await api.bank()).units);
      }}
    />`,

    dashboard: needsUnit(() => html`<${Dashboard}
      doc=${unitDoc}
      error=${unitDoc ? '' : panelError}
      onReload=${() => guard('reading the unit', readUnit).catch(() => {})}
      samples=${samples[unitId] || []}
      marks=${marks[unitId] || []}
      doctorReport=${doctorReport}
      busy=${steady}
      readOnly=${readOnly}
      ...${actions}
    />`),

    settings: needsUnit(() => html`<${SettingsTab}
      registers=${settings}
      runtime=${unitDoc?.runtime}
      switches=${switches}
      error=${settings ? '' : panelError}
      readOnly=${readOnly}
      onReload=${() => setSettings(null)}
    />`),

    cells: needsUnit(() => html`<${CellsTab}
      registers=${settings}
      runtime=${unitDoc?.runtime}
      readOnly=${readOnly}
    />`),

    ports: needsUnit(() => html`<${PortsTab}
      doc=${ports}
      error=${ports ? '' : panelError}
      readOnly=${readOnly}
      onReload=${() => setPorts(null)}
    />`),

    history: needsUnit(() => html`<${HistoryTab}
      doc=${history}
      codes=${logCodes}
      busy=${steady}
      onRead=${async () =>
        setHistory(await guard('reading the history', () => api.history(unitId)))}
      onDump=${async (bytes) =>
        setHistory(await guard('reading the flash dump', () => api.historyDump(bytes)))}
      onCodes=${async () =>
        setLogCodes((await guard('reading the code table', () => api.logCodes())).codes)}
    />`),

    registers: needsUnit(() => html`<${RegistersTab}
      registers=${registers}
      error=${registers ? '' : panelError}
      readOnly=${readOnly}
      onReload=${() => setRegisters(null)}
    />`),

    firmware: needsUnit(() => html`<${FirmwareTab}
      unitId=${unitId}
      library=${library}
      libraryError=${libraryError}
      busy=${steady}
      readOnly=${readOnly}
      job=${latestFlash(jobs)}
      onLibrary=${async (dir) => {
        setLibraryError('');
        try {
          setLibrary(await api.library(unitId, dir));
        } catch (err) {
          setLibraryError(err.message);
        }
      }}
      onCheck=${(bytes) => api.checkFirmware(unitId, bytes)}
      onFlash=${(bytes, force) =>
        guard('flashing', () => api.flashFirmware(unitId, bytes, force))}
    />`),

    tools: needsUnit(() => html`<${ToolsTab}
      unitId=${unitId}
      busy=${steady}
      readOnly=${readOnly}
      onAction=${async (doc) => {
        if (doc.settings) {
          await writeSettings(doc.settings);
          return;
        }
        await guard('the action', () => api.runAction({ id: unitId, ...doc }));
        remember(doc.action ? `ran ${doc.action}` : 'ran an action');
        toast.ok('Done');
        setDashboard(null);
        setSettings(null);
      }}
      onPresetPlan=${(preset) => writeSettings({}, { preset, dryRun: true })}
      onPreset=${async (preset) => {
        const doc = await writeSettings({}, { preset });
        if (doc.changes.length) remember(`applied the ${preset} preset`);
      }}
      onExport=${() => guard('exporting', () => api.exportSettings(unitId))}
      trace=${link?.trace}
      autoTrace=${autoTrace}
      onTrace=${async (action) => {
        await guard('the trace', () => traceCall(action));
        /* Said out loud: starting one changes nothing anybody can see on
         * the page for as long as the bus is idle, and a control that
         * appears to do nothing is a control people press twice. */
        toast.ok(
          {
            start: 'Recording every frame on the bus.',
            stop: 'Stopped. The recording is still here to download.',
            clear: 'Recording thrown away.',
          }[action]
        );
      }}
      onTraceReport=${() => guard('the trace report', () => api.traceReport())}
      onImportPlan=${(text) =>
        guard('reading the file', () =>
          api.importSettings({ id: unitId, file: text, dryRun: true })
        )}
      onImport=${async (text) => {
        await guard('importing', () =>
          api.importSettings({ id: unitId, file: text, dryRun: false })
        );
        remember('imported a settings file');
        toast.ok('Imported');
        setSettings(null);
      }}
    />`),
  };

  /* On or off, and nothing else: the beat is set in the pill's menu, and
   * leaving the interval out is how the server is told to keep it. */
  const setLive = (on) => guard('live updates', () => api.live(on)).catch(() => {});

  return html`<div class="app">
    <${Header} state=${server.state} nav=${nav} label="What to look at">
      <${Brand}><${Glyph} mark=${MARK} /><//>
      <!-- The name of the board is itself the way to change board: it opens
           a list, not a dropdown.  A bare select could say twenty characters
           about a board and could not say which of them was in trouble, and
           it was the one control on either page that looked like nothing
           else on it; alfenctl opens a dialog to choose a station and this
           is the same dialog.  What opens it used to be a "change" button
           beside the name, which is a second control for an action about
           the thing next to it. -->
      <${DeviceId}
        primary=${idOf(unit, unitId)}
        secondary=${madeOf(unit)}
        onPick=${units.length ? () => setPicking(true) : null}
        title="Which board on the bus"
      />
      <span class="spacer"></span>
      <!-- Every edit not sent yet, on whichever tab and card it was made,
           where it can always be seen and sent or dropped all at once.
           Nothing at all until something has been changed.  After the
           spacer, so the pill and the switches to its right do not move
           when it appears. -->
      <${PageApply} />
      <!-- In the header, not above the page.  As a bar of its own it
           appeared and disappeared with the server and pushed the tabs and
           everything under them down and back on every blip -- movement
           caused by the one message on the page that is not about the
           battery.  The header has hundreds of pixels of slack in it and a
           height set by the two lines naming the unit, so one line of small
           text beside them costs nothing. -->
      <${OfflineNotice} state=${server.state} onRetry=${refreshState} program=${NAME} />
      ${readOnly ? html`<${Badge} tone="info">read-only<//>` : null}
      <${LinkPill}
        link=${link}
        offline=${offline}
        readOnly=${readOnly}
        onLink=${(action) => guard('the port', () => api.link(action)).catch(() => {})}
        onLive=${(on, interval) => guard('live updates', () => api.live(on, interval)).catch(() => {})}
        onChoosePort=${() => setChoosing(true)}
        onWatchers=${() => setWatching(true)}
      />
      <!-- After the pill, not before it.  The pill is the one thing in this
           header whose width is decided by what it has to say -- "busy",
           "port held", "opening" -- and a switch that walks away from the
           pointer while the port is working is a switch you miss. -->
      <${LiveToggle} link=${link} offline=${offline} onLive=${setLive} program=${NAME} />
      <${Bell} alerts=${alerts} toast=${toast} />
      <${ThemeToggle} theme=${theme} />
    <//>

    <div class="body">
      <main>
        ${!link?.port || choosing
          ? html`<${PortPicker}
              busy=${steady}
              current=${link?.port ? { port: link.port, baud: link.baud } : null}
              problem=${portProblem}
              onCancel=${link?.port
                ? () => {
                    setChoosing(false);
                    setPortProblem('');
                  }
                : null}
              onChoose=${async (doc) => {
                setPortProblem('');
                /* Claimed before the port is opened, not after it answers:
                 * the link event naming the new port arrives while this is
                 * still waiting, and the read it would otherwise wake is a
                 * sweep of the bus nobody has been told about yet. */
                bankPort.current = doc.port;
                let answer;
                try {
                  /* Quietly: whatever went wrong belongs on the picker,
                   * under the list it went wrong about, and not in a toast
                   * that has faded by the time the next attempt is made. */
                  answer = await guard('opening the port', () => api.choosePort(doc), {
                    quiet: true,
                  });
                } catch (err) {
                  /* Named once.  "cannot open /dev/ttyUSB1: ..." already
                   * says which port it was about. */
                  setPortProblem(
                    err.message.includes(doc.port) ? err.message : `${doc.port}: ${err.message}`
                  );
                  setChoosing(true);
                  return;
                }
                if (answer.units.length) {
                  rememberPort(doc);
                  setChoosing(false);
                  loadBank(doc.port);
                  toast.ok(`Found ${answer.units.length} unit(s) on ${doc.port}`);
                  openUnit(answer.units[0].id);
                } else {
                  setPortProblem(
                    `Nothing answered on ${doc.port}. All sixteen addresses were ` +
                      'tried and none replied: either this is not the adapter the ' +
                      'battery is on, or A and B are swapped.'
                  );
                  setChoosing(true);
                }
              }}
            />`
          : TABS.map(
              ([id]) => html`<${Pane} key=${id} id=${id} shown=${tab === id}>
                ${tab === id ? (panels[id] ? panels[id]() : html`<${Empty}>No such tab.<//>`) : null}
              <//>`
            )}
      </main>

      <${License} />
    </div>

    ${picking
      ? html`<${BoardPicker}
          rows=${bank}
          units=${units}
          selected=${unitId}
          onPick=${(id) => {
            setPicking(false);
            openUnit(id);
          }}
          onClose=${() => setPicking(false)}
        />`
      : null}

    ${watching
      ? html`<${Watchers} me=${me} onClose=${() => setWatching(false)} toast=${toast} />`
      : null}

    <${DraftDialog} />
    <${Toasts} toasts=${toast.toasts} dismiss=${toast.dismiss} />
  </div>`;
}

/* The same four facts a scan files under a board, out of the nameplate a
 * dashboard read brought back.  The two documents name them differently --
 * the scan's row is what the header's picker is drawn from and the
 * dashboard's is the identity card's -- and this is the one place that has
 * to know both spellings. */
function fromIdentity(unitId, identity) {
  if (unitId === null || !identity) return null;
  return {
    id: unitId,
    model: identity.model,
    hardware: identity.hardwareVersion,
    version: identity.softwareVersion,
    serial: identity.serialNumber,
  };
}

function latestFlash(jobs) {
  const all = Object.values(jobs).filter((j) => j.name.startsWith('flashing'));
  return all.sort((a, b) => b.id - a.id)[0] || null;
}

/* Firefox will not raise a window from a content script and does not
 * highlight a tab whose title changed, so a title that blinks is the only
 * cue this page can give in a crowded tab strip. */
function flashTitle() {
  const original = document.title;
  let n = 0;
  const timer = setInterval(() => {
    document.title = n % 2 ? '‹ jkctl ›' : original;
    n += 1;
    if (n > 7) {
      clearInterval(timer);
      document.title = original;
    }
  }, 400);
}

render(html`<${App} />`, document.getElementById('root'));
