/* One unit's dashboard: what the pack is doing, and what it is set to do.
 *
 * The first row says which board this is and what it is doing -- the pack,
 * the cells, and the nameplate that names the thing the other two measure.
 * The second is what it has been doing and what it is set to do: the probes,
 * the history this page has kept, the three switches.  The last holds the
 * two cards that are only worth reading when something is wrong, and a pack
 * in trouble still says so from down there, because the protections card
 * counts what is raised on its own badge.
 *
 * "Row" is a figure of speech: the grid fills itself at whatever width it is
 * given, three cards across on a desktop and two or one below that, so the
 * order here is the order they are read in and the rows are what that order
 * comes out as on a wide screen.
 *
 * A card that would be mostly air is folded into its neighbour: the clock
 * lives in *Identity*, because a clock nobody set is part of who the unit
 * is, and the temperatures share a card with the sensors the board says are
 * missing, because that is the same question.
 */

import { useDraft } from '/core/js/drafts.js';
import { PanelError } from '/core/js/panels.js';
import {
  Badge,
  Card,
  ClockRows,
  DASH,
  duration,
  Empty,
  fixed,
  HealthCard,
  Row,
  Stat,
  SyncClock,
} from '/core/js/ui.js';
import { html } from '/core/vendor/preact-htm.module.js';
import {
  BATTERY_BAND,
  bandNames,
  CardBands,
  CELL_BAND,
  MOS_TEMPERATURES,
  PackBand,
} from './bands.js';
import { CellChart, History } from './chart.js';
import { hintOf, SETTINGS } from './panels.js';

function value(fields, key) {
  return fields?.[key]?.value ?? null;
}

function text(fields, key) {
  return fields?.[key]?.text ?? DASH;
}

/* --- the pack ------------------------------------------------------------ */

function Pack({ runtime, limits }) {
  const f = runtime.fields;
  const current = value(f, 'batCurrent');
  const flow =
    typeof current !== 'number' || Math.abs(current) < 0.05
      ? 'idle'
      : current > 0
        ? 'charging'
        : 'discharging';
  return html`<${Card}
    title="Pack"
    badge=${html`<${Badge} tone=${flow === 'idle' ? '' : 'good'}>${flow}<//>`}
  >
    <div class="big">${fixed(value(f, 'socRelativeStateOfCharge'), 0)}<span class="unit">% charged</span></div>
    <div class="stats spaced">
      <${Stat} k="voltage">${text(f, 'batVol')}<//>
      <${Stat} k="current">${text(f, 'batCurrent')}<//>
      <${Stat} k="power">${text(f, 'batWatt')}<//>
    </div>
    <${Row} k="Remaining" v=${text(f, 'socCapabilityRemain')} hint="The charge left in the pack, by the board's count." />
    <${Row}
      k="Full charge"
      v=${text(f, 'socFullChargeCapacity')}
      hint="What the board has learned the pack holds when full; the design capacity is on the Settings tab."
    />
    <${Row}
      k="State of health"
      v=${text(f, 'sOCSOH')}
      hint="State of health (SOH): the full charge as a share of the design capacity."
    />
    <${Row} k="Cycles" v=${text(f, 'socCycleCount')} hint="Full charge cycles the board has counted." />
    <${Row}
      k="Cycle capacity"
      v=${text(f, 'socCycleCapacity')}
      hint="All the charge the pack has delivered, added up over its life."
    />
    <${Row} k="Running for" v=${duration(value(f, 'runtime'))} />
    <${PackBand}
      registers=${limits}
      cells=${runtime.cellCount}
      voltage=${value(f, 'batVol')}
    />
  <//>`;
}

/* --- the cells ----------------------------------------------------------- */

const CELL_BANDS = [{ limits: CELL_BAND, reading: 'cells' }];

function Cells({ runtime, limits, readOnly }) {
  const f = runtime.fields;
  const draft = useDraft(SETTINGS, { names: bandNames(CELL_BANDS) });
  const wide = value(f, 'maxVoltDelta') > 0.05;
  const balance = runtime.balance;
  return html`<${Card}
    title="Cells"
    draft=${draft}
    badge=${html`${balance
      ? html`<${Badge}
          tone="info"
          title=${`The balancer is moving charge from cell ${balance.from} to cell ${balance.to}${
            typeof balance.current === 'number' ? ` at ${fixed(balance.current, 2)} A` : ''
          }.`}
          >balancing ${balance.from} → ${balance.to}<//
        > `
      : null}<${Badge} tone=${wide ? 'warn' : ''}>${runtime.cellCount} in series<//>`}
    help=${{
      summary: 'One bar per cell, and the voltages they may not leave.',
      body: html`The bars' scale is the span of the cells rather than zero to four volts: on a
        healthy pack every bar would otherwise be the same height. While the balancer runs,
        the two cells it is working on are striped: the highest, which it takes charge
        from (marked ▼), and the lowest, which it gives it to (▲). JK's boards say which
        two those are and not how much each moves, so the bars say no more than that. Hovering a bar gives that cell's voltage and the
        resistance of its balance wire; the whole table of wire resistances, beside the
        values this unit is configured with, is on the Cells &amp; balancing tab.
        <br /><br />
        Under them is the same pack on the scale the <em>protections</em> are set in, which
        the bars cannot show — they are drawn between the lowest cell and the highest, which
        is the only scale a spread of three millivolts is visible on. The dots are the
        setpoints, and they can be dragged; nothing is written until Apply, which shows the
        plan first.`,
    }}
  >
    <${CellChart} cells=${runtime.cells} balance=${balance} />
    <${CardBands}
      bands=${CELL_BANDS}
      registers=${limits}
      runtime=${runtime}
      draft=${draft}
      disabled=${readOnly}
    />
    <div class="stats spaced">
      <${Stat} k="average">${text(f, 'cellVolAve')}<//>
      <${Stat} k="spread" title="The highest cell's voltage less the lowest's.">
        ${text(f, 'maxVoltDelta')}
      <//>
      <${Stat} k="balance current" title="The current the balancer is moving between cells now.">
        ${text(f, 'equCurrent')}
      <//>
    </div>
  <//>`;
}

/* --- the protections ----------------------------------------------------- */

function Protections({ runtime }) {
  const raised = runtime.alarms || [];
  return html`<${Card}
    title="Protections"
    badge=${raised.length
      ? html`<${Badge} tone="bad">${raised.length} raised<//>`
      : html`<${Badge} tone="good">none raised<//>`}
  >
    ${raised.length
      ? html`<ul class="list">
          ${raised.map(
            (a) => html`<li key=${`${a.register}-${a.bit}`}>
              <${Badge} tone="bad">${a.state}<//>
              <span>${a.name}</span>
            </li>`,
          )}
        </ul>`
      : html`<${Empty}>Nothing is raised. Every protection bit this unit carries reads normal.<//>`}
  <//>`;
}

/* --- temperatures -------------------------------------------------------- */

const TEMPERATURES = [
  ['tempMos', 'MOS'],
  ['batTemp1', 'Battery 1'],
  ['batTemp2', 'Battery 2'],
  ['batTemp3', 'Battery 3'],
  ['batTemp4', 'Battery 4'],
  ['batTemp5', 'Battery 5'],
];

const TEMPERATURE_BANDS = [
  { limits: BATTERY_BAND, reading: 'probes' },
  { limits: MOS_TEMPERATURES, reading: 'mos' },
];

function Temperatures({ runtime, limits, readOnly }) {
  const f = runtime.fields;
  const draft = useDraft(SETTINGS, { names: bandNames(TEMPERATURE_BANDS) });
  // Named by the unit's own bit names rather than numbered here: bit 0 is the
  // MOS probe, so counting from one sends somebody looking at the wrong wire.
  //
  // `sensorsPresent` is which probes the unit says it has, and is what picks
  // the rows: a pack with two battery probes has no third to report.  It is
  // null when the unit did not answer for the word, and then the readings
  // themselves pick the rows, as they did before there was a word to ask.
  // This card used to read that word upside down and put "six sensors absent"
  // under six sensors and their temperatures.  `sensorsSilent` is the fault
  // that is left: a probe the unit still counts that has stopped reading.
  const present = runtime.sensorsPresent;
  const silent = runtime.sensorsSilent || [];
  const rows = TEMPERATURES.filter(([key, label]) =>
    present ? present.includes(label) : f?.[key],
  );
  return html`<${Card}
    title="Temperatures"
    draft=${draft}
    badge=${silent.length ? html`<${Badge} tone="warn">a sensor is not reading<//>` : null}
    help=${{
      summary: 'Each reading against the band the protections allow.',
      body: html`42 °C is fine under a 60 °C over-temperature protection and a fault under a
        40 °C one, so the bands are drawn rather than left as numbers on another tab. The
        first is the pack: every battery probe falls inside the stretch drawn on it, between
        the two under-temperature protections and the two over-temperature ones. The second
        is the MOSFETs, which run hotter than the cells by design and have a limit of their
        own. The setpoints can be dragged; the release points beside them, and the heater,
        are on the Settings tab.`,
    }}
  >
    ${rows.map(([key, label]) => html`<${Row} key=${key} k=${label} v=${text(f, key)} />`)}
    <${CardBands}
      bands=${TEMPERATURE_BANDS}
      registers=${limits}
      runtime=${runtime}
      draft=${draft}
      disabled=${readOnly}
    />
    ${silent.length
      ? html`<div class="caveat">
          <span>!</span>
          <span>
            The unit counts ${silent.length} temperature sensor(s) as connected that report
            no temperature: ${silent.join(', ')}. A pack protected by a probe that fell off
            has no over-temperature protection on that end of it.
          </span>
        </div>`
      : null}
  <//>`;
}

/* --- the three switches -------------------------------------------------- */

/* What each switch is, and what turning it off actually does.  Two of the
 * three are the pack's own contactors, and turning one off is the click
 * that opens it. */
const SWITCHES = [
  ['charge', 'Charging', 'batChargeEn', 'stops the pack taking any charge'],
  ['discharge', 'Discharging', 'batDischargeEn', 'cuts the pack off from whatever it is running'],
  ['balance', 'Balancing', 'balanEn', 'stops the balancer working on the cells'],
];

/* The three main switches, as a draft like everything else on the page.
 *
 * They were written on the click, once, which made the most consequential
 * control on the page -- turning discharging off cuts the pack off from
 * whatever it is running -- the only one that did not show its plan and
 * ask first.  They are the same three registers the Settings tab lists, so
 * a switch flipped here is pending there too. */
function Switches({ limits, readOnly }) {
  const draft = useDraft(SETTINGS, { names: SWITCHES.map(([, , key]) => key) });
  return html`<${Card}
    title="Switches"
    draft=${draft}
    help=${{
      summary: 'The three main switches, sent together when applied.',
      body: html`These are ordinary settings registers rather than action slots, and each
        takes effect the moment it is written. A pack with charging disabled is not faulty
        — somebody turned it off. Charging and discharging are the pack's contactors: what
        they do, they do to whatever is wired to it, which is why a change here shows its
        plan and asks before anything is written.`,
    }}
  >
    ${SWITCHES.map(([what, label, key, effect]) => {
      const reg = (limits || []).find((r) => r.name === key);
      const on = reg?.value === 1;
      if (!reg?.answered) {
        return html`<${Row}
          key=${what}
          k=${label}
          v=${html`<span class="muted">not mapped on this board</span>`}
          title="this board did not answer for the register behind this switch"
        />`;
      }
      const held = draft.has(key);
      const want = held ? draft.edits[key] === '1' : on;
      const says = want
        ? `${label} ${held ? 'will be' : 'is'} on. Turned off, it ${effect}.`
        : `${label} ${held ? 'will be' : 'is'} off.`;
      return html`<${Row}
        key=${what}
        k=${label}
        hint=${hintOf(reg)}
        pending=${held}
        v=${html`<label class=${held ? 'toggle pending' : 'toggle'} title=${says}>
          <span class="muted">${want ? 'on' : 'off'}</span>
          <input
            type="checkbox"
            checked=${want}
            disabled=${readOnly}
            onChange=${(e) => {
              const next = e.target.checked;
              /* Flipped back to what the board holds is not an edit. */
              if (next === on) draft.drop(key);
              else draft.set(key, next ? '1' : '0');
            }}
          />
        </label>`}
      />`;
    })}
    <div class="caveat">
      <span>!</span>
      <span>
        Charging and discharging are the pack's contactors. Turning one off stops power
        through it the moment it is written, so nothing is written until Apply is pressed
        and the plan it shows is confirmed.
      </span>
    </div>
  <//>`;
}

/* --- identity ------------------------------------------------------------ */

/* The board keeps a count of seconds and no time zone; its count is from
 * local midnight, which is how JK's own application sets it and reads it.
 * So local time is what it has, and what the row says. */
const BOARD_ZONE =
  "the board keeps no time zone: its clock counts from local midnight, as JK's own application sets it";

function Identity({ identity, clock, onSync, busy, readOnly }) {
  return html`<${Card}
    title="Identity"
    actions=${readOnly ? null : html`<${SyncClock} busy=${busy} onSync=${onSync} />`}
  >
    <${Row} k="Model" v=${identity.model || DASH} token />
    <${Row} k="Hardware" v=${identity.hardwareVersion || DASH} />
    <${Row} k="Firmware" v=${identity.softwareVersion || DASH} />
    <${Row} k="Serial number" v=${identity.serialNumber || DASH} token />
    <${Row} k="First powered on" v=${identity.manufactureDate || DASH} />
    <${Row} k="Power-ons" v=${identity.powerOnTimes ?? DASH} />
    <${Row} k="Total run time" v=${identity.totalRunTime || DASH} />
    <${ClockRows} clock=${clock} zone=${BOARD_ZONE} />
  <//>`;
}

/* --- health -------------------------------------------------------------- */

function Health({ report, onRun, busy }) {
  return html`<${HealthCard}
    report=${report}
    onRun=${onRun}
    busy=${busy}
    help=${{
      summary: 'The same pass `jkctl doctor` prints.',
      body: html`It is not run when the page opens: a full pass reads the nameplate, the
        runtime table and the whole configuration, which is three reads of a shared bus. One
        click answers "is this pack all right".`,
    }}
  />`;
}

/* --- the tab ------------------------------------------------------------- */

export function Dashboard({
  doc,
  error,
  onReload,
  samples,
  marks,
  onSync,
  onDoctor,
  doctorReport,
  busy,
  readOnly,
}) {
  /* A read that failed says so, and offers the way to try it again.  This
   * tab used to have only the one empty state -- "Reading this unit" --
   * which it also wore for a read that had already come back with an error
   * minutes ago, so a board that never answered was indistinguishable from
   * one that was slow.  Every other tab in this program says which it is;
   * see `PanelError`. */
  if (!doc && error) {
    return html`<${PanelError} error=${error} loading=${busy} onRetry=${onReload} />`;
  }
  if (!doc) return html`<${Empty}>Reading this unit…<//>`;
  const { runtime, identity, limits, clock } = doc;
  /* Three of these cards write, and they write the way every other card on
   * this page does: a draft of their own, an Apply that shows the plan
   * first, and nothing on the wire until it is confirmed.  What is dragged
   * or flipped here is the same register the Settings tab lists in a table. */
  const writes = { limits, readOnly };
  return html`<div class="grid">
    <${Pack} runtime=${runtime} limits=${limits} />
    <${Cells} runtime=${runtime} ...${writes} />
    <${Identity} identity=${identity} clock=${clock} onSync=${onSync} busy=${busy} readOnly=${readOnly} />
    <${Temperatures} runtime=${runtime} ...${writes} />
    <${Card}
      title="History"
      help=${{
        summary: "This page's own memory of what the pack has been doing.",
        body: html`The unit's registers say what is happening now and what has happened in
          total, and nothing in between. Every reading the live refresh brings is kept here,
          in this tab and on the server, so a reload comes back to the line it left. A
          dashed rule marks a moment something was written, so a change and its effect are
          next to each other. Power is drawn positive going in and negative coming out —
          the board reports it as a magnitude and puts the direction in the current, so the
          two are put back together here — and the rule across the middle is zero. The
          second line is the hottest probe, on its own scale, which is where a long charge
          shows up. The numbers under the chart are that same window added up: the energy
          charged and discharged, the highest and lowest power, and how far the state of
          charge, the pack voltage and the hottest probe moved over it.`,
      }}
    >
      <${History} samples=${samples} marks=${marks} />
    <//>
    <${Switches} ...${writes} />
    <${Protections} runtime=${runtime} />
    <${Health} report=${doctorReport} onRun=${onDoctor} busy=${busy} />
  </div>`;
}
