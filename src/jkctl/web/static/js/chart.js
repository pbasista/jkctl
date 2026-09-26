/* The two pictures this page draws, both in inline SVG.
 *
 * Neither exists for decoration.  The cell chart answers "is one of them
 * drifting", which sixteen numbers in a table do not: a bar is a length and
 * lengths compare at a glance.  The history line answers "I changed
 * something, did anything happen", which a single live number cannot answer
 * at all -- the registers say what is happening now and what has happened in
 * total, and nothing in between.
 */

import { Hovered, Series, useHover } from '/core/js/chart.js';
import { DASH, fixed, Stat } from '/core/js/ui.js';
import { html } from '/core/vendor/preact-htm.module.js';

/* One bar per cell, with the highest and the lowest called out.
 *
 * The scale is the span of the cells themselves rather than zero to four
 * volts: on a healthy pack every bar would otherwise be the same height, and
 * a chart on which nothing can be seen is worse than no chart.  The scale is
 * printed under it, because a bar chart with a floating baseline exaggerates
 * a spread of three millivolts into a cliff unless it says so.
 *
 * What each bar is came off the browser's own `title`, which is a second's
 * wait before a box in the system's font appears somewhere near the pointer
 * -- long enough that running along sixteen cells to find the odd one meant
 * sixteen pauses.  It is the shared hover label instead, the same one the
 * charger's energy chart uses: it is there the moment the pointer is over a
 * bar, it is in the page's own type, and it sits in a known place above the
 * chart rather than under the cursor.
 */
/* What the balancer is doing to one cell, in words: the tooltip's line,
 * and the reason a bar is marked. */
function balanceLine(cell, balance) {
  if (!balance) return null;
  if (cell.balance === 'giving') return `balancing: giving charge to cell ${balance.to}`;
  if (cell.balance === 'taking') return `balancing: taking charge from cell ${balance.from}`;
  return null;
}

export function CellChart({ cells, balance }) {
  const hover = useHover();
  const volts = cells.map((c) => c.voltage).filter((v) => typeof v === 'number');
  if (!volts.length) return html`<div class="empty">This unit reported no cell voltages.</div>`;
  const high = Math.max(...volts);
  const low = Math.min(...volts);
  // A floor of 20 mV keeps a perfectly balanced pack from being drawn as
  // sixteen wildly different bars because of one millivolt of noise.
  const span = Math.max(high - low, 0.02);
  const base = low - span * 0.15;
  const top = high + span * 0.15;

  /* Which bar the pointer is over.  The bars are equal slots across the
   * whole box with no pad at either end, so the fraction across the box is
   * the index straight away -- and the labels under them are outside the
   * box being measured, so they cannot shift the count by a row. */
  const at =
    hover.at === null ? -1 : Math.min(cells.length - 1, Math.floor(hover.at * cells.length));
  const under = at < 0 ? null : cells[at];

  return html`<div class="chart">
    <div class="plot" ref=${hover.box} ...${hover.on}>
      <div
        class="cellbars"
        role="img"
        aria-label=${`${cells.length} cells between ${fixed(low, 3)} and ${fixed(high, 3)} volts`}
      >
        ${cells.map((cell, i) => {
          const value = cell.voltage;
          const height = value === null ? 0 : ((value - base) / (top - base)) * 100;
          const tone = value === high ? 'high' : value === low ? 'low' : '';
          /* A cell the balancer is working on is striped, and marked
           * above with which way the charge is going: down out of the cell
           * it is taking from, up into the one it is giving to. */
          const cls = [
            'cellbar',
            tone,
            cell.balance ? `balancing ${cell.balance}` : '',
            i === at ? 'under' : '',
          ]
            .filter(Boolean)
            .join(' ');
          return html`<div class=${cls} key=${cell.cell} style=${`height:${height}%`}></div>`;
        })}
      </div>
      ${under &&
      html`<${Hovered}
        x=${(at + 0.5) / cells.length}
        y=${null}
        rule=${false}
        heading=${`Cell ${under.cell}`}
        lines=${[
          `${fixed(under.voltage, 3)} V`,
          under.resistance ? `wire ${fixed(under.resistance, 3)} Ω` : null,
          balanceLine(under, balance),
          under.voltage === high && high !== low ? 'highest' : null,
          under.voltage === low && high !== low ? 'lowest' : null,
        ].filter(Boolean)}
      />`}
    </div>
    <div class="celllabels">
      ${cells.map((cell) => html`<span key=${cell.cell}>${cell.cell}</span>`)}
    </div>
    <div class="muted" style="font-size:var(--fs-xs);margin-top:6px">
      scale ${fixed(base, 3)}–${fixed(top, 3)} V · highest cell ${cells.findIndex((c) => c.voltage === high) + 1},
      lowest ${cells.findIndex((c) => c.voltage === low) + 1}
    </div>
  </div>`;
}

/* The history line: what the pack has been doing, and what came of it.
 *
 * The drawing is `Series` in /core/js/chart.js -- the same chart, from the
 * same file, as the one on the charger's dashboard.  This was its own forty
 * lines of SVG until it was looked at: the line was stroked `var(--blue)`
 * and the write marks `var(--amber)`, and neither of those tokens has
 * existed since the palettes were named for what a colour means rather than
 * for what it is.  A custom property that resolves to nothing leaves the
 * property invalid at computed-value time, which for `stroke` means the
 * initial value, which is black -- so on the dark theme the chart drew a
 * black line on a near-black card and appeared to be broken.  Nothing said
 * so: it is an attribute on an SVG element, which no stylesheet check
 * looks at.
 *
 * Two readings on it, not one.  The power is what the pack is doing and the
 * hottest probe is what that is costing it, and the second of those is a
 * line that barely moves -- which is exactly why it does not deserve a card
 * of its own and does deserve to be next to the first.  They are on their
 * own scales, in their own colours, named by the key underneath.
 *
 * Under the chart are the numbers the chart cannot say: a line shows that
 * power went in, and only arithmetic says how much.  They are the same idea
 * as the per-phase readings on the charger's live meter -- the detail a
 * single big number leaves out -- worked out over exactly the window that is
 * drawn above them.
 */

/* Which way the power is going, and how much of it.
 *
 * The board reports the two halves separately and only one of them is
 * signed: `batWatt` is a u32 in the datasource -- a magnitude -- and the
 * direction is in `batCurrent`, which is an i32.  So a chart drawn straight
 * from `batWatt` draws an hour of charging and an hour of discharging as
 * the same hill, which is the one thing this chart exists to tell apart.
 */
function flow(sample) {
  if (typeof sample.power !== 'number') return null;
  return typeof sample.current === 'number' && sample.current < 0
    ? -sample.power
    : sample.power;
}

/* Longer than this between two samples and nothing is assumed to have
 * happened in between.  The beat is three seconds; a gap is live updates
 * switched off, a laptop asleep, or the page in a background tab, and
 * carrying the last power reading across an hour of that would invent
 * kilowatt-hours the pack never moved. */
const GAP_S = 30;

/* What the window shown adds up to: the energy charged and discharged, and
 * the highest and lowest power the pack was seen at.
 *
 * Trapezoidal, because a reading is a point and the truth between two of
 * them is closer to a ramp than to a step -- and because a pack ramping
 * down through zero otherwise counts a discharge as a charge for one beat.
 * Each interval is split at zero for the same reason.
 *
 * The highest and lowest are of the signed power, so on a window that was
 * all discharge both are negative: "lowest" is the hardest discharge and
 * "highest" the gentlest, which is what the chart above them shows too.
 */
function totals(points) {
  let charged = 0;
  let discharged = 0;
  let high = points.length ? points[0].v : null;
  let low = high;
  for (let i = 1; i < points.length; i += 1) {
    const dt = points[i].t - points[i - 1].t;
    const value = points[i].v;
    high = Math.max(high, value);
    low = Math.min(low, value);
    if (dt <= 0 || dt > GAP_S) continue;
    const mean = (points[i - 1].v + value) / 2;
    if (mean > 0) charged += (mean * dt) / 3600;
    else discharged -= (mean * dt) / 3600;
  }
  return { charged, discharged, high, low };
}

/* An energy in the unit it is readable in.  A pack that has taken 40 Wh in
 * ten minutes should say so; one that has taken 4 kWh should not say 4000. */
function energy(wh) {
  return Math.abs(wh) >= 1000
    ? `${fixed(wh / 1000, 2)} kWh`
    : `${fixed(wh, wh >= 100 ? 0 : 1)} Wh`;
}

/* How far a reading moved across the window, signed, or null when there is
 * not enough of it to say. */
function moved(points, field) {
  const known = points.filter((s) => typeof s[field] === 'number');
  if (known.length < 2) return null;
  return known[known.length - 1][field] - known[0][field];
}

function signed(value, digits, unit) {
  if (value === null) return DASH;
  return `${value > 0 ? '+' : ''}${fixed(value, digits)} ${unit}`;
}

/* A power, signed the way the chart draws it -- positive into the pack --
 * with the direction spelt out on hover, since a bare "-850 W" asks the
 * reader to remember which way round the sign goes. */
function Power({ watts, what }) {
  const way = watts > 0 ? 'charging' : watts < 0 ? 'discharging' : 'neither charging nor discharging';
  return html`<span title=${`${fixed(Math.abs(watts), 0)} W ${way}${what ? ` -- ${what}` : ''}`}>
    ${signed(watts, 0, 'W')}
  </span>`;
}

/* The figures under the chart, and what each is called.
 *
 * They had been named in prose -- "taken in", "given out", "hardest",
 * "charge moved", "pack moved", "warmed by" -- which was friendly and could
 * not be looked up: "hardest" is not a word anybody uses of a power, and
 * "warmed by" says nothing when the pack cooled.  They are now the words a
 * battery's own documentation uses.  Energy is *charged* and *discharged*;
 * power is signed, positive into the pack as the chart draws it, and the
 * window's lowest and highest are given as the range it covered, each end
 * with its direction on hover; and a reading that moved across the window
 * is its *change*, end minus start, signed.  "SOC" is the abbreviation the
 * board itself, the Pack card and every inverter manual use for the state
 * of charge.  The temperature is the hottest probe, which is what the
 * second line on the chart is.
 */
export function History({ samples, marks = [] }) {
  const points = (samples || [])
    .map((s) => ({ t: s.t, v: flow(s) }))
    .filter((s) => s.v !== null);
  const last = points.length ? points[points.length - 1] : null;
  const sums = totals(points);
  const socMoved = moved(samples || [], 'soc');
  const tempMoved = moved(samples || [], 'temp');
  const voltMoved = moved(samples || [], 'voltage');
  return html`<div>
    ${last === null
      ? null
      : html`<div class="big light">${fixed(last.v, 0)}<span class="unit">W</span></div>`}
    <${Series}
      samples=${points}
      marks=${marks}
      unit="W"
      digits=${0}
      step=${100}
      floor=${100}
      label="power"
      also=${{
        samples: (samples || [])
          .filter((s) => typeof s.temp === 'number')
          .map((s) => ({ t: s.t, v: s.temp })),
        unit: '°C',
        digits: 1,
        step: 2,
        label: 'hottest probe',
      }}
      what="pack power"
      empty="Nothing recorded yet — turn live updates on and this fills in."
    />
    ${points.length > 1
      ? html`<div class="stats spaced" style="margin-top:10px">
          <${Stat} k="charged" title="energy that went into the pack over this window">
            ${energy(sums.charged)}
          <//>
          <${Stat} k="discharged" title="energy that came out of the pack over this window">
            ${energy(sums.discharged)}
          <//>
          <${Stat} k="power range">
            <${Power} watts=${sums.low} what="the lowest power in this window" />${' to '}<${Power}
              watts=${sums.high}
              what="the highest power in this window"
            />
          <//>
        </div>
        <div class="stats spaced">
          <${Stat} k="SOC change" title="state of charge at the end of this window, minus at its start">
            ${signed(socMoved, 0, '%')}
          <//>
          <${Stat} k="voltage change" title="pack voltage at the end of this window, minus at its start">
            ${signed(voltMoved, 2, 'V')}
          <//>
          <${Stat}
            k="temperature change"
            title="the hottest probe at the end of this window, minus at its start"
          >
            ${signed(tempMoved, 1, '°C')}
          <//>
        </div>`
      : null}
  </div>`;
}
