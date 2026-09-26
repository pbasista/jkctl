/* The register browser: everything the board answers for, searchable.
 *
 * The vendor's application shows fixed panels, and between them the other
 * tabs here leave two thirds of what a JK board reports undisplayed -- the
 * PWM duty cycles, the relay and pre-discharge states, the six
 * protection-release countdowns, the MCU id, the sensor-presence bits.  This
 * is the tab that answers questions the other tabs were not designed for, and
 * it is nearly free: the datasource already describes every field, so what a
 * register *is* travels with its value.
 *
 * A register JK's own map marks writable can be edited here.  One that it
 * does not is shown and not offered, rather than offered and refused at the
 * last moment -- guessing that a register is settable because its name sounds
 * settable is how a battery gets reconfigured by accident.
 */

import { useDraft } from '/core/js/drafts.js';
import { PanelError } from '/core/js/panels.js';
import { Badge, Card, DASH, Empty, Select } from '/core/js/ui.js';
import { html, useMemo, useState } from '/core/vendor/preact-htm.module.js';
import { boundsOf, hintOf, SETTINGS } from './panels.js';

const TABLES = [
  ['', 'every table'],
  ['01', 'settings (0x1000)'],
  ['02', 'runtime (0x1200)'],
  ['03', 'device info (0x1400)'],
];

export function RegistersTab({ registers, error, readOnly, onReload }) {
  const [filter, setFilter] = useState('');
  const [table, setTable] = useState('');
  const [writableOnly, setWritableOnly] = useState(false);
  const [unmapped, setUnmapped] = useState(true);
  /* The same draft every card of settings is a view of: a setting changed
   * on another tab is pending in its row here, and one changed here is
   * pending there.  It is sent the one way too, plan first. */
  const draft = useDraft(SETTINGS);

  const rows = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (registers || []).filter((r) => {
      if (table && r.table !== table) return false;
      if (writableOnly && !r.writable) return false;
      if (!unmapped && !r.answered) return false;
      if (!needle) return true;
      return (
        [r.name, r.label, r.vendor, r.description].some((said) =>
          (said || '').toLowerCase().includes(needle),
        )
      );
    });
  }, [registers, filter, table, writableOnly, unmapped]);

  if (error) return html`<${PanelError} error=${error} onRetry=${onReload} />`;
  if (!registers) return html`<${Empty}>Reading every table…<//>`;

  const answered = (registers || []).filter((r) => r.answered).length;

  return html`<div>
    <div class="wrap gap">
      <input
        type="search"
        placeholder="name or description…"
        value=${filter}
        onInput=${(e) => setFilter(e.target.value)}
        style="min-width:16rem"
      />
      <${Select} value=${table} entries=${TABLES} onChange=${setTable} />
      <label class="toggle">
        <input type="checkbox" checked=${writableOnly} onChange=${(e) => setWritableOnly(e.target.checked)} />
        <span class="muted">writable only</span>
      </label>
      <label class="toggle">
        <input type="checkbox" checked=${unmapped} onChange=${(e) => setUnmapped(e.target.checked)} />
        <span class="muted">show unmapped</span>
      </label>
      <span style="flex:1"></span>
      <span class="muted">${rows.length} of ${registers.length} · ${answered} answered</span>
    </div>

    <div class="gap">
    <${Card} title="Registers" width="full" draft=${draft}>
      <div class="table-wrap tall">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
              <th class="num">Value</th>
              <th>Unit</th>
              <th class="num">Range</th>
              <th class="num">Default</th>
              <th>Access</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map((reg) => html`<${Line} key=${`${reg.table}-${reg.name}`} reg=${reg} draft=${draft} readOnly=${readOnly} />`)}
          </tbody>
        </table>
      </div>
      ${!rows.length ? html`<${Empty}>Nothing matches.<//>` : null}
    <//>
    </div>
  </div>`;
}

function Line({ reg, draft, readOnly }) {
  const held = draft.edits[reg.name];
  const changed = held !== undefined;
  const editable = reg.writable && !readOnly && reg.answered && reg.kind !== 'array';
  const range =
    reg.minimum !== null && reg.maximum !== null ? `${reg.minimum} … ${reg.maximum}` : DASH;
  // An array's full value is in `value`; the text is an abbreviation, so the
  // whole of it goes in the cell's tooltip rather than into the column width.
  const full = Array.isArray(reg.value) ? reg.value.join(' ') : null;
  return html`<tr class=${changed ? 'pending' : ''}>
    <td class="mono">${reg.name}</td>
    <td title=${hintOf(reg)}>
      ${reg.label || html`<span class="muted">no description</span>`}
    </td>
    <td class="num" title=${full || (reg.long ? reg.text : undefined) || boundsOf(reg) || undefined}>
      ${!reg.answered
        ? html`<span class="muted">not mapped</span>`
        : editable
          ? html`<input
              type=${reg.kind === 'text' ? 'text' : 'number'}
              class=${changed ? 'num pending' : 'num'}
              style="width:7rem"
              value=${changed ? held : (reg.value ?? '')}
              onInput=${(e) => draft.set(reg.name, e.target.value)}
            />`
          : reg.text}
    </td>
    <td class="muted">${reg.unit || ''}</td>
    <td class="num muted">${range}</td>
    <td class="num muted">${reg.default ?? DASH}</td>
    <td>
      ${reg.writable
        ? html`<${Badge} tone="info">rw<//>`
        : html`<${Badge} title="JK's register map does not mark this writable">r<//>`}
    </td>
  </tr>`;
}
