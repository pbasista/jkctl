/* The things you do to a unit rather than read from it.
 *
 * Every one of these is asked about first, with what it will do written out,
 * because each of them either replaces every protection setpoint, turns the
 * board off, or moves the address you are talking to.  The chemistry presets
 * are not the board's one-key slots but JK's published values for them,
 * planned like any settings edit: the dialog lists every setting and what
 * it goes to, because a one-key button whose effect nobody can predict is
 * how a bank ends up on the wrong setpoints.
 *
 * Export and import are here too. An import shows the same plan a settings
 * write does, and refuses to be a surprise: the file carries only the
 * writable fields, so nothing device-specific -- the serial number, the
 * run-time counters -- travels between units with it.
 */

import { PlanDialog } from '/core/js/drafts.js';
import { AutoTraceToggle } from '/core/js/trace.js';
import { Badge, Card, Confirm, duration, Empty, Row, Stat } from '/core/js/ui.js';
import { html, useState } from '/core/vendor/preact-htm.module.js';

/* The three slots the vendor document does not list: key, label, what it
 * does, and the word that has to be typed to mean it. */
const BOARD_ACTIONS = [
  ['restart', 'Restart board', 'The protection board restarts and stops answering until it is back.', 'restart'],
  [
    'factory-restore',
    'Factory restore',
    'Every setting goes back to the factory configuration. Yours are gone; export them first if they matter.',
    'restore',
  ],
  [
    'erase-data',
    'Erase all data',
    "The board's stored data goes, including its history and its counters.",
    'erase',
  ],
];

const PRESETS = [
  ['lifepo4', 'LiFePO₄'],
  ['li-ion', 'Li-ion'],
  ['lto', 'LTO'],
];

/* How many bytes a recording holds, in the units somebody sizes an
 * attachment in. */
function size(bytes) {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* The serial recorder, and the report it makes.
 *
 * What this answers is the question "it did not work, and the page said
 * four words about it".  A failed write raises "no/short response", which
 * names the symptom and nothing else: not which register the board refused,
 * not whether anything came back at all, not how long it waited.  All of
 * that is on the wire and none of it is kept, because keeping every frame
 * of a bus that is working is thousands of lines an hour of nothing.
 *
 * So it is switched on for as long as it takes to make the fault happen
 * again, and then downloaded.  The report is plain text with the bytes
 * decoded into words -- which board, which function, which register, and
 * which named settings that register covers -- so the file is readable by
 * somebody who has never seen this program.
 */
function SerialTrace({ trace, busy, onTrace, onReport, auto }) {
  const on = !!trace?.on;
  const frames = trace?.frames || 0;
  /* How long it has run, or ran: a stopped recording's clock stops with it. */
  const since = trace?.since ? (trace.until ? trace.until - trace.since : Date.now() / 1000 - trace.since) : null;
  return html`<${Card}
    title="Serial trace"
    badge=${on
      ? html`<${Badge} tone="warn">recording<//>`
      : frames
        ? html`<${Badge}>${frames} frame(s) held<//>`
        : null}
    help=${{
      summary: 'Keep every frame on the bus, for a fault that needs explaining.',
      body: html`Turn it on, make the thing go wrong again, turn it off, and download the
        report. It records both directions — what this program asked and what the board
        answered, including the silences, which is what a timeout actually is — and decodes
        each frame into words: the address, the function, the register and the settings that
        register covers. It also records what this page asked for, with the values you typed
        and the answer you were given, so the report says which button was pressed and not
        only what followed from it. Nothing is recorded while it is off, and nothing leaves
        this machine unless you send the file. The report holds your battery's configuration
        and the traffic that read it, and nothing else about you.
        <br /><br />
        With <em>Record on failure</em> on, you rarely have to start it yourself: the first
        time the board or the bus fails a request, recording starts and the notice saying
        so offers the file, and a failure while it is already running offers the file
        straight away. Turning it off stops a recording it started, and keeps what that
        recording holds; one you started yourself runs on until you stop it. That choice
        is kept in this browser.`,
    }}
    foot=${html`<div class="wrap">
      <button
        class=${on ? 'btn' : 'btn primary'}
        disabled=${busy}
        onClick=${() => onTrace(on ? 'stop' : 'start')}
      >
        ${on ? 'Stop recording' : 'Start recording'}
      </button>
      <button class="btn" disabled=${busy || !frames} onClick=${onReport}>
        Download the report
      </button>
      <button class="btn" disabled=${busy || !frames} onClick=${() => onTrace('clear')}>
        Throw it away
      </button>
    </div>`}
  >
    ${auto ? html`<${AutoTraceToggle} auto=${auto} />` : null}
    ${on || frames
      ? html`<div class="stats spaced">
          <${Stat} k="state">${on ? (trace?.automatic ? 'recording, since a failure' : 'recording') : 'stopped'}<//>
          <${Stat} k="frames">${frames}${trace?.dropped ? ` (+${trace.dropped} dropped)` : ''}<//>
          <${Stat} k="size">${size(trace?.bytes)}<//>
          <${Stat} k=${on ? 'running for' : 'ran for'}>${since === null ? '—' : duration(since)}<//>
        </div>`
      : html`<${Empty}>
          Nothing is being recorded. While this is off the bus costs nothing to watch.
        <//>`}
    ${trace?.dropped
      ? html`<div class="caveat">
          <span>!</span>
          <span>
            The recording is full at ${trace.limit} frames and the oldest are falling off
            the front. What is in the report is the most recent ${trace.limit}, which is
            what you want if the fault has just happened and not if it happened an hour ago.
          </span>
        </div>`
      : null}
  <//>`;
}

export function ToolsTab({
  unitId,
  onAction,
  onExport,
  onImportPlan,
  onImport,
  onPresetPlan,
  onPreset,
  trace,
  autoTrace,
  onTrace,
  onTraceReport,
  busy,
  readOnly,
}) {
  const [ask, setAsk] = useState(null);
  const [calibration, setCalibration] = useState({ voltage: '', current: '' });
  const [address, setAddress] = useState('');
  const [importPlan, setImportPlan] = useState(null);
  const [file, setFile] = useState(null);
  const [presetPlan, setPresetPlan] = useState(null);
  const [planning, setPlanning] = useState(false);

  const confirmed = async () => {
    const run = ask.run;
    setAsk(null);
    await run();
  };

  const readFile = async (event) => {
    const chosen = event.target.files?.[0];
    if (!chosen) return;
    const text = await chosen.text();
    setFile({ name: chosen.name, text });
    setImportPlan(await onImportPlan(text));
  };

  return html`<div class="grid">
    <${SerialTrace}
      trace=${trace}
      auto=${autoTrace}
      busy=${busy}
      onTrace=${onTrace}
      onReport=${onTraceReport}
    />

    <${Card}
      title="Chemistry presets"
      width="wide"
      help=${{
        summary: "JK's defaults for a cell chemistry, shown before they are written.",
        body: html`Each button plans JK's published defaults for that chemistry — cell
          voltages, protection delays and temperatures, nineteen settings — and shows every
          one that would change before anything is written. The values are the table in
          JK's BD-series manual, the only list JK has published; the board's own one-key
          slots choose theirs in firmware, unseen, and are left to
          <span class="mono">jkctl preset --one-key</span>. The table does not give the
          charge and float voltages, the SOC voltages or where balancing starts, so those
          stay as they are: after changing chemistry, set them on the Settings tab before
          charging. JK's table stops charging at
          −20 °C for all three chemistries, colder than lithium iron phosphate makers allow; the plan
          shows it, so it can be changed afterwards on the Settings tab.`,
      }}
    >
      <div class="wrap">
        ${PRESETS.map(
          ([key, label]) => html`<button
            key=${key}
            class="btn"
            disabled=${busy || readOnly || planning}
            onClick=${async () => {
              setPlanning(true);
              try {
                const doc = await onPresetPlan(key);
                setPresetPlan({ key, label, changes: doc.changes });
              } finally {
                setPlanning(false);
              }
            }}
          >
            ${label}
          </button>`,
        )}
      </div>
      ${presetPlan &&
      html`<${PlanDialog}
        plan=${presetPlan.changes}
        title=${`Apply the ${presetPlan.label} preset: ${presetPlan.changes.length} change${presetPlan.changes.length === 1 ? '' : 's'}?`}
        onCancel=${() => setPresetPlan(null)}
        onConfirm=${async () => {
          const key = presetPlan.key;
          setPresetPlan(null);
          await onPreset(key);
        }}
      />`}
    <//>

    <${Card}
      title="Board"
      help=${{
        summary: 'Emergency start, and turning the board off.',
        body: html`The emergency start brings a pack back up after a protection has latched
          it off; it starts a battery rather than reconfiguring one, so it is the one action
          here that changes no setting. Shutdown powers the protection board down, and it
          will stop answering on this bus afterwards.`,
      }}
    >
      <div class="wrap">
        <button
          class="btn"
          disabled=${busy || readOnly}
          onClick=${() =>
            setAsk({
              title: `Fire the emergency start on BMS ${unitId}?`,
              body: html`<p>
                This is for use in emergencies. Improperly activating it may cause permanent
                damage to the battery — which is JK's own warning, and worth repeating.
              </p>`,
              run: () => onAction({ action: 'emergency' }),
            })}
        >
          Emergency start
        </button>
        <button
          class="btn danger"
          disabled=${busy || readOnly}
          onClick=${() =>
            setAsk({
              title: `Shut BMS ${unitId} down?`,
              danger: true,
              typed: 'shutdown',
              body: html`<p>
                The protection board powers down and stops answering on this bus. Bringing it
                back needs whatever wakes it physically — a charger, or the button.
              </p>`,
              run: () => onAction({ action: 'shutdown' }),
            })}
        >
          Shut down
        </button>
        <button
          class="btn"
          disabled=${busy || readOnly}
          onClick=${() => onAction({ action: 'time-sync' })}
        >
          Sync the clock
        </button>
      </div>
    <//>

    <${Card}
      title="Undocumented board actions"
      width="wide"
      badge=${html`<${Badge} tone="warn">recovered, not documented<//>`}
      help=${{
        summary: "Three slots JK's own register map does not list.",
        body: html`They were read out of the vendor application's own buttons: each of its
          "Send" buttons fires one action slot, and the same reading gives 0x04 for
          "Shutdown Board" — which the document <i>does</i> list at 0x04, which is what says
          the reading is right. None of the three has been fired at a real board by this
          program. A factory restore takes your whole configuration with it and an erase
          takes the history along with it, so each asks with the word typed out.`,
      }}
    >
      <div class="wrap">
        ${BOARD_ACTIONS.map(
          ([key, label, what, word]) => html`<button
            key=${key}
            class="btn danger"
            disabled=${busy || readOnly}
            onClick=${() =>
              setAsk({
                title: `${label} on BMS ${unitId}?`,
                danger: true,
                typed: word,
                body: html`<div>
                  <p>${what}</p>
                  <p class="caveat">
                    <span>!</span>
                    <span>
                      This slot's address comes from the vendor's application rather than
                      from its register map, and this program has never fired it at real
                      hardware.
                    </span>
                  </p>
                </div>`,
                run: () => onAction({ action: key }),
              })}
          >
            ${label}
          </button>`,
        )}
      </div>
    <//>

    <${Card}
      title="Calibration"
      help=${{
        summary: 'Tell the unit what the pack really reads.',
        body: html`Give the true reading from a meter you trust and the unit corrects its own
          measurement against it. Voltage is in millivolts and current in milliamps, which is
          the resolution the registers hold; a negative current is a discharge.`,
      }}
    >
      <${Row}
        k="Pack voltage"
        v=${html`<span class="wrap" style="justify-content:flex-end">
          <input
            class="num"
            type="number"
            placeholder="mV"
            value=${calibration.voltage}
            onInput=${(e) => setCalibration({ ...calibration, voltage: e.target.value })}
          />
          <button
            class="btn"
            disabled=${busy || readOnly || !calibration.voltage}
            onClick=${() =>
              setAsk({
                title: 'Recalibrate the pack voltage?',
                danger: true,
                body: html`<p>
                  The unit will take ${calibration.voltage} mV as the true pack voltage and
                  correct every reading against it from now on.
                </p>`,
                run: () =>
                  onAction({ action: 'voltage-calibration', value: Number(calibration.voltage) }),
              })}
          >
            Set
          </button>
        </span>`}
      />
      <${Row}
        k="Pack current"
        v=${html`<span class="wrap" style="justify-content:flex-end">
          <input
            class="num"
            type="number"
            placeholder="mA"
            value=${calibration.current}
            onInput=${(e) => setCalibration({ ...calibration, current: e.target.value })}
          />
          <button
            class="btn"
            disabled=${busy || readOnly || !calibration.current}
            onClick=${() =>
              setAsk({
                title: 'Recalibrate the current shunt?',
                danger: true,
                body: html`<p>
                  The unit will take ${calibration.current} mA as the true pack current.
                  Calibrate this with a known load, not at rest.
                </p>`,
                run: () =>
                  onAction({ action: 'current-calibration', value: Number(calibration.current) }),
              })}
          >
            Set
          </button>
        </span>`}
      />
    <//>

    <${Card}
      title="Configuration file"
      width="wide"
      help=${{
        summary: 'Move a configuration between units, with the diff first.',
        body: html`The file carries only the writable fields, so nothing device-specific —
          the serial number, the run-time counters — travels with it. An import that would
          change nothing says so rather than rewriting sixty registers.`,
      }}
    >
      <div class="wrap">
        <button class="btn" disabled=${busy} onClick=${onExport}>
          Export BMS ${unitId}
        </button>
        <input type="file" accept=".json" onChange=${readFile} disabled=${readOnly} />
        ${file ? html`<span class="muted mono">${file.name}</span>` : null}
      </div>
      ${importPlan
        ? importPlan.changes.length
          ? html`<div style="margin-top:12px">
              <p class="muted">${importPlan.changes.length} setting(s) would change:</p>
              <ul class="list">
                ${importPlan.changes.map(
                  (c) => html`<li key=${c.name}>
                    <span class="mono">${c.name}</span>
                    <span class="muted">${c.oldText} →</span><b>${c.newText}</b>
                  </li>`,
                )}
              </ul>
              <button
                class="btn primary"
                style="margin-top:10px"
                disabled=${busy || readOnly}
                onClick=${() =>
                  setAsk({
                    title: `Write ${importPlan.changes.length} setting(s) to BMS ${unitId}?`,
                    body: html`<p>The changes listed on the tab behind this dialog.</p>`,
                    run: async () => {
                      await onImport(file.text);
                      setImportPlan(null);
                      setFile(null);
                    },
                  })}
              >
                Write them
              </button>
            </div>`
          : html`<${Empty}>This unit already matches that file.<//>`
        : null}
    <//>

    <${Card}
      title="Bus address"
      badge=${html`<${Badge}>answering at ${unitId}<//>`}
      help=${{
        summary: 'The address stored on the board.',
        body: html`Changing it changes which address reaches this unit. On most JK boards the
          four-position DIP switch selects the address and this register follows it, so check
          the switches before relying on this.`,
      }}
    >
      <${Row}
        k="Set devAddr to"
        v=${html`<span class="wrap" style="justify-content:flex-end">
          <input
            class="num"
            type="number"
            min="0"
            max="15"
            value=${address}
            onInput=${(e) => setAddress(e.target.value)}
          />
          <button
            class="btn danger"
            disabled=${busy || readOnly || address === ''}
            onClick=${() =>
              setAsk({
                title: `Change BMS ${unitId} to address ${address}?`,
                danger: true,
                typed: 'address',
                body: html`<p>
                  Afterwards this unit answers at ${address}, and the page will have to scan
                  again to find it.
                </p>`,
                run: () => onAction({ settings: { devAddr: address } }),
              })}
          >
            Change
          </button>
        </span>`}
      />
    <//>

    ${ask
      ? html`<${Confirm}
          title=${ask.title}
          body=${ask.body}
          danger=${ask.danger}
          typed=${ask.typed}
          confirmLabel="Do it"
          onCancel=${() => setAsk(null)}
          onConfirm=${confirmed}
        />`
      : null}
  </div>`;
}
