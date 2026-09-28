/* The board's own stored fault records, and what their codes mean.
 *
 * A JK board keeps a history: a snapshot of the whole pack at each moment
 * something tripped -- a timestamp, a code, the switch positions, the highest
 * and lowest cell, the pack voltage and current, the capacity and the
 * temperatures. That snapshot is the best diagnostic either vendor
 * application offers, and it is what somebody wants at eight in the morning
 * after a battery cut out at three.
 *
 * The records are not served over Modbus: the firmware bounds a read to
 * frames 01-03 and keeps the records where only Bluetooth reaches them, so the
 * over-the-wire "Read the records" button will come up empty on stock
 * firmware and says why. The way to get them through jkctl is a full flash
 * dump of a patched board (jkctl firmware dump-flash) -- the records live in
 * that image, and "Load a flash dump" decodes them here in the browser.
 *
 * The code table is worth having on its own either way: these are the
 * vendor's own words for every event one of its boards records.
 */

import { Badge, Card, DASH, Empty, fixed } from '/core/js/ui.js';
import { html, useRef, useState } from '/core/vendor/preact-htm.module.js';

export function HistoryTab({ doc, codes, busy, onRead, onDump, onCodes }) {
  const [showCodes, setShowCodes] = useState(false);
  const fileRef = useRef(null);

  const pickDump = async (event) => {
    const chosen = event.target.files?.[0];
    event.target.value = '';
    if (!chosen) return;
    await onDump(new Uint8Array(await chosen.arrayBuffer()));
  };

  return html`<div class="grid">
    <${Card}
      title="Stored fault records"
      width="full"
      badge=${doc
        ? doc.records.length
          ? html`<${Badge} tone="good">${doc.records.length} record(s)<//>`
          : doc.source === 'dump'
            ? html`<${Badge}>none in this dump<//>`
            : html`<${Badge}>not over the wire<//>`
        : null}
      help=${{
        summary: 'What the board wrote down the last time something tripped.',
        body: html`Each record carries the pack as it was at that moment: the highest and
          lowest cell and their numbers, the pack voltage and current, what was left of the
          capacity, three temperatures, and which switches were closed. This board's firmware
          does not serve these over the serial (RS485/Modbus) link — that is a limit of the
          firmware, not of jkctl — so to see them here, take a full flash dump of a patched
          board with <b>jkctl firmware dump-flash</b> and load it below.`,
      }}
      foot=${html`<div class="wrap">
        <button class="btn" disabled=${busy} onClick=${() => fileRef.current?.click()}>
          Load a flash dump…
        </button>
        <input
          ref=${fileRef}
          type="file"
          accept=".bin"
          style="display:none"
          onChange=${pickDump}
        />
        <button class="btn ghost" disabled=${busy} onClick=${() => onRead()}>
          Try over the wire
        </button>
        <button
          class="btn ghost"
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
      <div class="notice">
        <p>
          These records are not available over the serial (RS485/Modbus) link. This board's
          firmware does not implement reading them there, so it is a limitation of the
          firmware, not a fault in jkctl — <b>Try over the wire</b> asks the board anyway,
          since another board's firmware may expose them now or in a future version. The
          vendor's mobile app reads them over <b>Bluetooth</b> instead. To read them here
          today, take a full flash dump of a patched board (<b>jkctl firmware dump-flash</b>)
          and <b>load the dump</b>.
        </p>
      </div>
      ${!doc
        ? html`<${Empty}>No records loaded yet — load a flash dump, above.<//>`
        : !doc.supported
          ? html`<div class="notice">${doc.why}</div>`
          : !doc.records.length
            ? html`<${Empty}>${
                doc.source === 'dump'
                  ? 'No stored records in this image — its record region is erased, or it is not a full dump of a supported board.'
                  : 'No records came back over the wire. Stock firmware does not serve them there; load a flash dump instead.'
              }<//>`
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
