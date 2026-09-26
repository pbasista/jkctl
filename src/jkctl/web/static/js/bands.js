/* The setpoints this unit is holding, drawn where the readings are.
 *
 * A JK board keeps about a dozen voltages and temperatures that are all
 * answers to the same question -- how far the pack is allowed to go before
 * something trips -- and every one of them was a number in a table on the
 * Settings tab, a tab away from the cells and the probes they are about.
 * A number in a table cannot say that a pack sitting at 3.48 V a cell is
 * nowhere near its over-voltage protection, or that a release point was
 * set below the protection it releases, both of which a picture says at a
 * glance.
 *
 * The picture is `Band` from the shared package, the same one the charger
 * draws its temperature alarms with.  What is here is the join between it
 * and a *register*: a register already describes itself -- its label, its
 * unit, its decimals, the range the datasource will allow -- so a band is
 * named by a list of register names and everything else follows from what
 * the device said about them.
 *
 * Dragging edits the card's draft and nothing else.  The same rule as
 * every other control on this page: Apply shows the plan `jkctl settings
 * set --dry-run` would print, and the plan is what writes.
 */

import { Band, trackFor } from '/core/js/band.js';
import { html } from '/core/vendor/preact-htm.module.js';

function has(value) {
  return value !== null && value !== undefined && !Number.isNaN(value);
}

/* What a band draws for one register: the value the card is holding if it
 * is holding one, the value the unit answered with, and the bounds the
 * datasource says the register will accept. */
function handleFor(row, spec, draft, byName) {
  const held = draft?.edits?.[row.name];
  const value = held === undefined ? row.value : Number(held);
  if (!has(value)) return null;
  const step = row.decimals ? 10 ** -row.decimals : 1;
  /* A release point may not be set past the protection it releases, and a
   * floor may not be set past its ceiling.  The relationship is named by
   * the caller because only the caller knows it: two under-temperature
   * protections, one for charging and one for discharging, are unrelated
   * and may sit in either order. */
  const other = (name) => {
    const found = byName.get(name);
    if (!found) return null;
    const heldOther = draft?.edits?.[name];
    const got = heldOther === undefined ? found.value : Number(heldOther);
    return has(got) ? got : null;
  };
  const above = spec.above ? other(spec.above) : null;
  const below = spec.below ? other(spec.below) : null;
  return {
    key: row.name,
    label: spec.label || row.label || row.name,
    value,
    saved: row.value,
    side: spec.side || '',
    soft: Boolean(spec.soft),
    /* Its own resolution, not the band's.  Most of these bands hold
     * registers of one kind and one number of decimals, but not all:
     * `Start Balance Volt.` is kept to ten millivolts on a band of cell
     * voltages kept to one, and the arrow keys have to move it by the
     * step it is written back at or the move is rounded away. */
    step,
    note: spec.note || null,
    fixed: !row.writable || !!row.refused,
    min: Math.max(row.minimum ?? -Infinity, above === null ? -Infinity : above + step),
    max: Math.min(row.maximum ?? Infinity, below === null ? Infinity : below - step),
  };
}

/* A band of registers.
 *
 * `names` is `{ name, side, soft, above, below, note }` each, in the order
 * they belong on the scale.  A register this board did not answer for
 * simply is not drawn -- the same rule the register cards follow -- and a
 * band nothing answered for is not drawn at all, rather than as an empty
 * track that looks like a device with no limits.
 */
function RegisterBand({
  registers,
  names,
  draft,
  disabled,
  now,
  nowLabel,
  spread,
  spreadLabel,
  label,
  caption,
  foot,
  pad,
}) {
  const byName = new Map((registers || []).map((r) => [r.name, r]));
  const rows = names.map((spec) => [byName.get(spec.name), spec]).filter(([row]) => row?.answered);
  const handles = rows.map(([row, spec]) => handleFor(row, spec, draft, byName)).filter(Boolean);
  if (!handles.length) return null;

  const first = rows[0][0];
  const decimals = first.decimals ?? 0;
  const step = decimals ? 10 ** -decimals : 1;
  /* Measured from what the unit is holding and from the reading, never from
   * the draft: a scale that moved as a setpoint was dragged would slide
   * every other setpoint on it out from under the hand.  Then widened for
   * anything the draft has put outside it, which is a value typed into the
   * row beside the picture rather than dragged on it. */
  const track = trackFor([...handles.map((h) => h.saved), now, spread?.low, spread?.high], {
    pad,
    round: step,
  });
  if (!track) return null;
  const drafted = handles.map((h) => h.value).filter(has);
  const min = Math.min(track.min, ...drafted);
  const max = Math.max(track.max, ...drafted);

  const settable = Boolean(draft) && !disabled && handles.some((h) => !h.fixed);
  return html`<${Band}
    min=${min}
    max=${max}
    step=${step}
    digits=${decimals}
    unit=${first.unit || ''}
    now=${has(now) ? now : null}
    nowLabel=${nowLabel || 'now'}
    spread=${spread && has(spread.low) && has(spread.high) ? spread : null}
    spreadLabel=${spreadLabel || 'reading'}
    label=${label}
    caption=${caption}
    foot=${foot}
    handles=${handles}
    onChange=${settable
      ? (key, value) => draft.set(key, value.toFixed(byName.get(key)?.decimals ?? 0))
      : null}
  />`;
}

/* --- the bands this program draws ----------------------------------------
 *
 * Named here rather than written out at each of the three places they
 * appear, because the dashboard and the settings tab must not disagree
 * about which setpoints bound a cell: the same picture, wherever it is
 * drawn, or it is two pictures.
 */

/* The four protections a cell lives between, and the two voltages the unit
 * releases them at.  The balancer's start voltage is on the same scale and
 * is not a protection, so it is a mark rather than a limit. */
export const CELL_LIMITS = [
  { name: 'volCellUV', side: 'low', below: 'volCellUVPR' },
  { name: 'volCellUVPR', side: 'low', soft: true, above: 'volCellUV', note: 'release' },
  { name: 'volStartBalan', note: 'balancing starts above this' },
  { name: 'volCellOVPR', side: 'high', soft: true, below: 'volCellOV', note: 'release' },
  { name: 'volCellOV', side: 'high', above: 'volCellOVPR' },
];

/* The same four, without the releases: the dashboard answers "is this pack
 * inside its limits", and a release point is not one of its limits. */
export const CELL_BAND = [
  { name: 'volCellUV', side: 'low' },
  { name: 'volStartBalan', note: 'balancing starts above this' },
  { name: 'volCellOV', side: 'high' },
];

/* Where the charge is aimed, rather than where it is stopped.  None of
 * these is a protection -- the pack is meant to reach them -- so they are
 * all marks, and the band has no calm stretch because there is nothing
 * here to be outside of. */
export const CHARGE_TARGETS = [
  { name: 'volSOCP0' },
  { name: 'volCellRFV' },
  { name: 'volCellRCV' },
  { name: 'volSOCP100' },
];

/* What the cells are allowed to be, in temperature.  Charging a lithium
 * cell below freezing plates lithium onto the anode and does not undo
 * itself, which is why the charge under-temperature protection is drawn
 * with the rest rather than left on its own. */
export const BATTERY_TEMPERATURES = [
  { name: 'tmpBatCUT', side: 'low', below: 'tmpBatCUTPR' },
  { name: 'tmpBatCUTPR', side: 'low', soft: true, above: 'tmpBatCUT', note: 'release' },
  { name: 'tmpBatDCHUT', side: 'low', below: 'tmpBatDCHUTPR' },
  { name: 'tmpBatDCHUTPR', side: 'low', soft: true, above: 'tmpBatDCHUT', note: 'release' },
  { name: 'tmpBatCOTPR', side: 'high', soft: true, below: 'tmpBatCOT', note: 'release' },
  { name: 'tmpBatCOT', side: 'high', above: 'tmpBatCOTPR' },
  { name: 'tmpBatDcOTPR', side: 'high', soft: true, below: 'tmpBatDcOT', note: 'release' },
  { name: 'tmpBatDcOT', side: 'high', above: 'tmpBatDcOTPR' },
];

/* The four hard ones, for the dashboard. */
export const BATTERY_BAND = [
  { name: 'tmpBatCUT', side: 'low' },
  { name: 'tmpBatDCHUT', side: 'low' },
  { name: 'tmpBatCOT', side: 'high' },
  { name: 'tmpBatDcOT', side: 'high' },
];

/* The MOSFETs are their own band: they are the switch rather than the
 * cells, they run hotter than the cells by design, and drawing a healthy
 * MOS against the pack's limits would put it outside a band that has
 * nothing to do with it. */
export const MOS_TEMPERATURES = [
  { name: 'tmpMosOTPR', side: 'high', soft: true, below: 'tmpMosOT', note: 'release' },
  { name: 'tmpMosOT', side: 'high', above: 'tmpMosOTPR' },
];

/* When the balancer is allowed to work. */
export const BALANCE_LIMITS = [{ name: 'volStartBalan', note: 'balancing starts above this' }];

/* The pack's own voltage, against the per-cell protections multiplied by
 * the number of cells in series.
 *
 * The board protects cells, not packs: there is no pack over-voltage
 * register to set.  So these two ends are worked out rather than read, and
 * they are drawn as something that cannot be dragged -- a control that
 * moved would have to divide by the cell count and write a per-cell
 * setting, which is a different setting from the one it appears to be.
 * The cell band above it is where that is set.
 */
function packHandles(registers, cells) {
  const byName = new Map((registers || []).map((r) => [r.name, r]));
  const at = (name) => {
    const row = byName.get(name);
    return row?.answered && has(row.value) ? row.value : null;
  };
  const low = at('volCellUV');
  const high = at('volCellOV');
  if (!cells || low === null || high === null) return [];
  return [
    {
      key: 'packLow',
      label: 'Cell UVP × cells',
      value: low * cells,
      side: 'low',
      fixed: true,
      note: 'set per cell',
    },
    {
      key: 'packHigh',
      label: 'Cell OVP × cells',
      value: high * cells,
      side: 'high',
      fixed: true,
      note: 'set per cell',
    },
  ];
}

/* The pack band cannot go through `RegisterBand`: its two ends are not
 * registers.  It is the one band on the page drawn from arithmetic. */
export function PackBand({ registers, cells, voltage }) {
  const handles = packHandles(registers, cells);
  if (!handles.length || !has(voltage)) return null;
  const track = trackFor([...handles.map((h) => h.value), voltage], { round: 1 });
  if (!track) return null;
  return html`<${Band}
    min=${track.min}
    max=${track.max}
    step=${0.01}
    digits=${2}
    unit="V"
    now=${voltage}
    nowLabel="pack now"
    label="pack voltage against the cell protections multiplied by the cell count"
    handles=${handles}
    caption="The pack, against the cell protections multiplied up"
    foot=${`${cells} cells in series`}
  />`;
}

/* --- what the unit is reading, on the scale a band is drawn in -----------
 *
 * A limit means nothing without the reading beside it, and the three
 * readings these bands are about are three shapes.  A pack has one
 * voltage.  Its cells have sixteen, which is a stretch and not a point --
 * drawing the average would hide the one cell that is running away, which
 * is the whole reason to look.  Its probes are the same: the pack is as
 * cold as its coldest end and as hot as its hottest.
 */

/* The lowest and the highest cell, as a stretch. */
function cellSpread(runtime) {
  const volts = (runtime?.cells || []).map((c) => c.voltage).filter(has);
  if (!volts.length) return null;
  return { low: Math.min(...volts), high: Math.max(...volts) };
}

/* The coldest and the hottest battery probe.  The MOS probe is not one of
 * them: it is the switch, not the cells.
 *
 * Only the probes the unit says it has.  A board with two of them wired
 * publishes a temperature for all five and the three that are not there
 * read zero, so a stretch taken from the readings alone ran from 0 °C to
 * whatever the pack is at -- a pack drawn as freezing at one end, on the
 * picture whose whole job is to say whether it is.  The unit's own
 * `tempSensorAbsent` word is what picks them, the same word the card picks
 * its rows with.
 */
const PROBES = [
  ['batTemp1', 'Battery 1'],
  ['batTemp2', 'Battery 2'],
  ['batTemp3', 'Battery 3'],
  ['batTemp4', 'Battery 4'],
  ['batTemp5', 'Battery 5'],
];

function probeSpread(runtime) {
  const present = runtime?.sensorsPresent;
  const read = PROBES.filter(([, label]) => (present ? present.includes(label) : true))
    .map(([key]) => runtime?.fields?.[key]?.value)
    .filter(has);
  if (!read.length) return null;
  return { low: Math.min(...read), high: Math.max(...read) };
}

function mosTemperature(runtime) {
  const value = runtime?.fields?.tempMos?.value;
  return has(value) ? value : null;
}

/* What a band of each kind is drawn against: a reading, a stretch, and the
 * word the hover label names it with. */
function readingFor(kind, runtime) {
  if (kind === 'cells') {
    return {
      spread: cellSpread(runtime),
      spreadLabel: 'cells now',
      caption: 'Every cell, against the voltages it may not leave',
      label: 'cell voltages against their setpoints',
    };
  }
  if (kind === 'probes') {
    return {
      spread: probeSpread(runtime),
      spreadLabel: 'battery probes now',
      caption: 'Every battery probe, against the pack’s own limits',
      label: 'battery temperatures against their protections',
    };
  }
  if (kind === 'mos') {
    return {
      now: mosTemperature(runtime),
      nowLabel: 'MOS now',
      caption: 'The MOSFETs, which have a limit of their own',
      label: 'MOS temperature against its protection',
    };
  }
  return {};
}

/* Every band a card asks for, drawn in order.  A card whose registers this
 * board did not answer for gets nothing, which is what `RegisterBand`
 * already returns for an unanswered band. */
export function CardBands({ bands, registers, runtime, draft, disabled }) {
  if (!bands || !registers) return null;
  return bands.map((spec) => {
    // What the reading brings -- the stretch, the words for it -- and then
    // whatever the card says about this band in particular, which wins: two
    // cards draw the cells against setpoints that are not protections, and
    // "every cell, against the voltages it may not leave" is the wrong
    // sentence over either of them.
    const { limits, reading, ...own } = spec;
    const props = { ...readingFor(reading, runtime), ...own };
    return html`<${RegisterBand}
      key=${limits[0].name}
      registers=${registers}
      names=${limits}
      draft=${draft}
      disabled=${disabled}
      ...${props}
    />`;
  });
}

/* Every register a list of bands can move: what a card holding them counts
 * among its own edits, alongside the fields it lists. */
export function bandNames(bands) {
  return (bands || []).flatMap((band) => band.limits.map((row) => row.name));
}
