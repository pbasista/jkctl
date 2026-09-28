/* The bank: every board on the bus, one tile each.
 *
 * Neither vendor application has this view, and for anyone with more than one
 * battery it is the reason to open the page at all: four packs, four states
 * of charge, and the one that is drifting visible without opening anything.
 *
 * A tile leads with the state of charge because that is the number people
 * come for, carries the pack's voltage and current under it, and turns amber
 * or rose from its own readings -- a wide cell spread, a raised protection --
 * so the tile that needs attention is the tile that looks different.
 */

import { Badge, DASH, Empty, fixed, Lines, Picker, Stat } from '/core/js/ui.js';
import { html } from '/core/vendor/preact-htm.module.js';

/* Above this spread the balancer is not keeping up; JK's own trigger is far
 * below it, so this is "worth a look", not "broken". */
const SPREAD_WARN_V = 0.05;

function tileTone(row) {
  if (!row.ok) return 'gone';
  if (row.alarms?.length) return 'bad';
  if (typeof row.delta === 'number' && row.delta > SPREAD_WARN_V) return 'warn';
  /* A board that answered and is inside its limits.  Said rather than left
   * to the default, because the default is a tile for a device that has not
   * been read yet -- which is what alfenctl's fleet is full of. */
  return 'good';
}

function socTone(row) {
  if (row.alarms?.length) return 'bad';
  if (typeof row.soc === 'number' && row.soc < 20) return 'warn';
  return '';
}

function activity(row) {
  const words = [];
  if (row.charging) words.push('charging');
  if (row.discharging) words.push('discharging');
  if (row.balancing) words.push('balancing');
  if (row.heating) words.push('heating');
  return words;
}

/* The line that names the tile, in the header's shape.
 *
 * The serial number no two units share leads it, because that is what the
 * header and the board picker name a board by everywhere else -- the bank
 * tile was the one place that led with the address instead.  The Modbus
 * address follows it, lighter, because that is not the name but the handle:
 * it is in the URL, it is what `--id` takes, and it is what the DIP switches
 * on the board are set to, so which address a serial is at is worth saying.
 * A board that gave no serial is already titled by its address (`idOf`), so
 * the address is not then repeated after it. */
function nameLine(row) {
  return row.serial
    ? html`${idOf(row, row.id)} <span class="addr">BMS ${row.id}</span>`
    : idOf(row, row.id);
}

function Tile({ row, selected, onOpen }) {
  const tone = tileTone(row);
  if (!row.ok) {
    return html`<button class=${`tile ${tone}`} onClick=${() => onOpen(row.id)}>
      <div class="tile-head">
        <${Lines} primary=${nameLine(row)} secondary=${row.error || 'this address did not answer'} />
        <span class="spacer" style="flex:1"></span>
        <${Badge}>no answer<//>
      </div>
    </button>`;
  }
  const words = activity(row);
  return html`<button
    class=${`tile ${tone} ${selected ? 'selected' : ''}`.trim()}
    onClick=${() => onOpen(row.id)}
  >
    <div class="tile-head">
      <${Lines} primary=${nameLine(row)} secondary=${madeOf(row)} />
      <span class="spacer" style="flex:1"></span>
      ${row.alarms?.length
        ? html`<${Badge} tone="bad" title=${row.alarms.join('\n')}>${row.alarms.length} alarm${row.alarms.length === 1 ? '' : 's'}<//>`
        : html`<${Badge} tone="good">ok<//>`}
    </div>

    <div class="big">${fixed(row.soc, 0)}<span class="unit">% charged</span></div>
    <div class=${`soc-bar ${socTone(row)}`.trim()}>
      <span style=${`width:${Math.max(0, Math.min(100, row.soc ?? 0))}%`}></span>
    </div>

    <div class="stats spaced">
      <${Stat} k="pack">${fixed(row.voltage, 2)} V<//>
      <${Stat} k="current">${fixed(row.current, 2)} A<//>
      <${Stat} k="power">${fixed(row.power, 0)} W<//>
    </div>

    <div class="stats">
      <${Stat} k="cells">${fixed(row.cellLow, 3)}–${fixed(row.cellHigh, 3)} V<//>
      <${Stat} k="spread" tone=${row.delta > SPREAD_WARN_V ? 'warn' : ''}>
        ${fixed(row.delta, 3)} V
      <//>
      <${Stat} k="hottest">${fixed(row.temperature, 1)} °C<//>
    </div>

    ${words.length
      ? html`<div class="wrap" style="margin-top:8px">
          ${words.map((w) => html`<${Badge} key=${w} tone="good">${w}<//>`)}
        </div>`
      : null}
  </button>`;
}

export function Bank({ rows, selected, onOpen, onRescan, onChoosePort, busy, port }) {
  if (!rows?.length) {
    /* Two ways out, because there are two reasons to be here: the boards are
     * on this bus and were not asked at the right moment, or they are not on
     * this bus at all.  A page that only offered to scan again offered the
     * one that does not work to whoever had picked the wrong adapter. */
    return html`<div>
      <${Empty}>
        Nothing has answered on ${port ? html`<span class="mono">${port}</span>` : 'this bus'} yet.
      <//>
      <div class="wrap gap">
        <button class="btn" disabled=${busy} onClick=${onRescan}>Scan the bus</button>
        ${onChoosePort
          ? html`<button class="btn" onClick=${onChoosePort}>Choose another port…</button>`
          : null}
      </div>
    </div>`;
  }
  const alarms = rows.filter((r) => r.ok && r.alarms?.length).length;
  const silent = rows.filter((r) => !r.ok).length;
  return html`<div>
    <div class="toolbar">
      <span class="muted">
        ${rows.length} unit${rows.length === 1 ? '' : 's'} on this bus
        ${alarms ? html`· <b style="color:var(--danger)">${alarms} raising a protection</b>` : null}
        ${silent ? html`· ${silent} not answering` : null}
      </span>
      <span style="flex:1"></span>
      ${onChoosePort
        ? html`<button class="btn" onClick=${onChoosePort}>Change port…</button>`
        : null}
      <button class="btn" disabled=${busy} onClick=${onRescan}>Rescan</button>
    </div>
    <div class="tiles">
      ${rows.map(
        (row) => html`<${Tile} key=${row.id} row=${row} selected=${row.id === selected} onOpen=${onOpen} />`,
      )}
    </div>
  </div>`;
}

/* Which board on the bus this page is about, as a dialog.
 *
 * It was a `<select>` in the header: twenty characters per board, no way to
 * say which of them was raising a protection, and the one control on either
 * page that looked like nothing else on it.  This is `Picker` from the
 * shared package -- the same dialog alfenctl opens to choose a station.
 *
 * Two lists name the boards and neither is always the fuller one: the scan's
 * rows carry the state of charge and the alarms, and the link event's units
 * are what there is before a scan has landed -- and after a rescan, briefly,
 * the other way round.  So: every id either of them knows, described by the
 * scan's row where there is one.
 */
export function BoardPicker({ rows, units, selected, onPick, onClose }) {
  /* Merged field by field rather than row by row.  A scan's row and a link
   * event's unit describe the same board and not the same way -- the scan
   * knows the state of charge and the protections, the event knows the
   * nameplate -- and letting the later document replace the earlier one
   * dropped the serial number off every board the moment a scan landed,
   * which is the one thing the list is sorted by and named for. */
  const byId = new Map();
  for (const row of [...(units || []), ...(rows || [])]) {
    byId.set(row.id, { ...byId.get(row.id), ...row });
  }
  const known = [...byId.values()].sort((a, b) => a.id - b.id);
  const entries = known.map((row) => ({
    key: row.id,
    label: idOf(row, row.id),
    detail: madeOf(row),
    /* The address as well as the name.  A serial number is what tells two
     * matching boards apart, and the address is what reaches one: it is in
     * the URL, it is what `--id` takes, and it is what the DIP switches on
     * the board itself are set to. */
    aside: [`BMS ${row.id}`, row.ok === false ? 'did not answer' : null]
      .filter(Boolean)
      .join(' · '),
    tone: row.ok === false ? 'gone' : '',
    selected: row.id === selected,
  }));
  return html`<${Picker}
    title="Which board"
    entries=${entries}
    selected=${selected}
    empty="Nothing has answered on this bus yet. The Bank tab can scan it again."
    onPick=${(entry) => onPick(entry.key)}
    onClose=${onClose}
  />`;
}

/* The two lines a board is named by, in the header and in the dialog that
 * changes which board the page is about.
 *
 * The first has to be the one string no other board on the bus shares, which
 * is the serial number the unit was given at the factory -- the address is
 * only unique among the boards that happen to be plugged in, and the model is
 * shared by every one of them in a bank built out of matching units.  A board
 * that reports no serial falls back to its address, which is at least the
 * thing the DIP switches say.
 *
 * The second is what it is: the model, and the versions of the two halves
 * that answer -- because what a register means, and whether it can be written
 * at all, depends on the firmware and not on the model name.
 */
export function idOf(unit, unitId) {
  if (unit?.serial) return unit.serial;
  return unitId === null || unitId === undefined ? 'no unit' : `BMS ${unitId}`;
}

export function madeOf(unit) {
  if (!unit) return 'not read yet';
  /* Three facts, separated by spaces.  They were "JK_PB2A16S20P · hw 11.XW
   * · fw 11.34" -- dots and qualifiers on a line that was already the
   * longest thing in the header, and the first thing to be cut off on a
   * laptop.  Nothing is lost by dropping them: this is the line under a
   * serial number in a header, its three parts are in the order they are
   * in on every nameplate, and alfenctl has said "model firmware" in plain
   * spaces here all along.
   *
   * A part the board has not said is a dash rather than a gap.  A bank is
   * read down a column, and "JK_PB2A16S20P 11.34" beside "JK_PB2A16S20P
   * 11.XW 11.34" reads as two boards on different firmware rather than as
   * one board that has not said which hardware it is. */
  const parts = [unit.model, unit.hardware, unit.version];
  if (!parts.some(Boolean)) return 'nameplate unreadable';
  return parts.map((part) => part || DASH).join(' ');
}
