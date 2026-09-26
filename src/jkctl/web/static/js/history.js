/* The board's own stored fault records, and what their codes mean.
 *
 * A JK board keeps a history: it counts the records in its runtime table, and
 * the datasource describes each one to the byte -- a timestamp, a code, the
 * switch positions, and a snapshot of the whole pack at the moment it
 * tripped. That snapshot is the best diagnostic either vendor application
 * offers, and it is what somebody wants at eight in the morning after a
 * battery cut out at three.
 *
 * What is not settled is where the records live over Modbus. The vendor
 * application reads them over its other channel, and whether the Modbus
 * interface maps them anywhere is an open question -- so this tab reads the
 * window the pattern of the four documented frames points at, and when a
 * board does not answer there it says exactly that, which is a different
 * sentence from "your battery has no history".
 *
 * The code table is certain either way, and it is worth having on its own:
 * these are the vendor's own words for every event one of its boards records.
 */

import { Badge, Card, DASH, Empty, fixed } from '/core/js/ui.js';
import { html, useState } from '/core/vendor/preact-htm.module.js';

export function HistoryTab({ doc, codes, busy, onRead, onCodes }) {
  const [showCodes, setShowCodes] = useState(false);

  return html`<div class="grid">
    <${Card}
      title="Stored fault records"
      width="full"
      badge=${doc
        ? doc.supported
          ? html`<${Badge} tone="good">${doc.records.length} record(s)<//>`
          : html`<${Badge}>not mapped here<//>`
        : null}
      help=${{
        summary: 'What the board wrote down the last time something tripped.',
        body: html`Each record carries the pack as it was at that moment: the highest and
          lowest cell and their numbers, the pack voltage and current, what was left of the
          capacity, three temperatures, and which switches were closed. Which register
          window serves them is not documented and may not exist on your board — reading it
          is harmless either way, and this says which answer you got.`,
      }}
      foot=${html`<div class="wrap">
        <button class="btn" disabled=${busy} onClick=${() => onRead()}>Read the records</button>
        <button
          class="btn"
          disabled=${busy}
          onClick=${async () => {
            if (!codes) await onCodes();
            setShowCodes(!showCodes);
          }}
        >
          ${showCodes ? 'Hide' : 'Show'} the code table
        </button>
      </div>`}
    >
      ${!doc
        ? html`<${Empty}>Not read yet.<//>`
        : !doc.supported
          ? html`<div class="notice">${doc.why}</div>`
          : !doc.records.length
            ? html`<${Empty}>This board answered, and is holding no records.<//>`
            : html`<div class="table-wrap tall">
                <table>
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Event</th>
                      <th class="num">Pack</th>
                      <th class="num">Current</th>
                      <th class="num">Cells</th>
                      <th class="num">Remaining</th>
                      <th class="num">Temps</th>
                      <th>Closed</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${doc.records.map(
                      (r) => html`<tr key=${r.index}>
                        <td>${r.when ? new Date(r.when).toLocaleString() : DASH}</td>
                        <td>
                          ${r.name}
                          ${r.known ? null : html` <${Badge} title="no source names this code">?<//>`}
                        </td>
                        <td class="num">${fixed(r.packV, 2)} V</td>
                        <td class="num">${fixed(r.packA, 1)} A</td>
                        <td class="num">
                          ${fixed(r.cellMinV, 3)}–${fixed(r.cellMaxV, 3)} V
                          <span class="muted">(#${r.cellMinNo}, #${r.cellMaxNo})</span>
                        </td>
                        <td class="num">${fixed(r.remainingAh, 1)} / ${fixed(r.fullAh, 1)} Ah</td>
                        <td class="num">${r.minTempC}…${r.maxTempC} °C, MOS ${r.mosTempC}</td>
                        <td>${r.closed.join(', ') || DASH}</td>
                      </tr>`,
                    )}
                  </tbody>
                </table>
              </div>`}
    <//>

    ${showCodes && codes
      ? html`<${Card}
          title="What a record's code means"
          width="full"
          help=${{
            summary: "The vendor's own table, extracted from its application.",
            body: html`Every event a JK board can write down, with the words JK's own
              application shows for it. Codes 100–131 and 200–231 are the per-cell
              protections, one per cell.`,
          }}
        >
          <div class="table-wrap tall">
            <table>
              <thead>
                <tr>
                  <th class="num">Code</th>
                  <th>Event</th>
                </tr>
              </thead>
              <tbody>
                ${Object.entries(codes).map(
                  ([code, name]) => html`<tr key=${code}>
                    <td class="num mono">${code}</td>
                    <td>${name}</td>
                  </tr>`,
                )}
              </tbody>
            </table>
          </div>
        <//>`
      : null}
  </div>`;
}
