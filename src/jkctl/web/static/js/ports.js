/* The communication ports, the dry contacts and the buzzer.
 *
 * A JK board speaks to an inverter over one of its UARTs or over CAN, and
 * which protocol it speaks is a number indexing a list the vendor ships. That
 * list is in Chinese in both the English and the Chinese datasource, so
 * choosing "005" in the vendor's application means choosing between
 * forty-two lines an English-speaking installer cannot read. The English name
 * leads here and JK's own string is kept beside it, so the two screens can
 * still be compared.
 */

import { useDraft } from '/core/js/drafts.js';
import { PanelError } from '/core/js/panels.js';
import { Badge, Card, Empty, Select } from '/core/js/ui.js';
import { html } from '/core/vendor/preact-htm.module.js';
import { RegisterCard } from './panels.js';

/* The protocols are a draft of their own: they are not registers of the
 * settings table but selectors the server sets through a call of its own,
 * one port at a time (see the writer in app.js).  They used to be set a row
 * at a time, each with a Set button of its own -- the one control on the
 * page edited that way. */
export const PROTOCOLS = 'protocols';

export const PORT_KEYS = { UART1: 'uart1', UART2: 'uart2', UART3: 'uart3', UART4: 'uart4', CAN: 'can' };

export function PortsTab({ doc, error, readOnly, onReload }) {
  const draft = useDraft(PROTOCOLS);

  if (error) return html`<${PanelError} error=${error} onRetry=${onReload} />`;
  if (!doc) return html`<${Empty}>Reading the ports…<//>`;

  const settable = new Map(doc.selectors.map((s) => [s.name, s]));

  return html`<div>
    <div class="grid">
    <${Card}
      title="Port protocols"
      width="wide"
      draft=${draft}
      help=${{
        summary: 'Which protocol each serial port and the CAN port speaks.',
        body: html`A change may need a restart before the unit speaks the new protocol.
          UART3 and UART4 are read here and cannot be set: JK's register map describes a
          two-port board and marks neither of them writable, and a register the document
          does not list as writable is not one to guess at.`,
      }}
    >
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Port</th>
              <th>Protocol</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${doc.ports.map((port) => {
              const word = PORT_KEYS[port.port];
              const spec = settable.get(word);
              const kind = spec?.kind || 'uart';
              const list = doc.lists[kind] || [];
              const held = draft.has(word);
              const current = held ? draft.edits[word] : port.number;
              const vendor = list.find((p) => p.id === current)?.vendor;
              return html`<tr key=${port.port} class=${held ? 'pending' : ''}>
                <td><b>${port.port}</b></td>
                <td>
                  ${spec?.settable && !readOnly
                    ? html`<${Select}
                        value=${current}
                        pending=${held}
                        entries=${list.map((p) => [p.id, p.name])}
                        onChange=${(v) => {
                          /* Put back to what the port speaks is not an edit. */
                          if (Number(v) === port.number) draft.drop(word);
                          else draft.set(word, Number(v));
                        }}
                      />`
                    : html`<span>${port.protocol}</span>`}
                  ${vendor ? html`<div class="muted" style="font-size:var(--fs-xs)">${vendor}</div>` : null}
                </td>
                <td>
                  ${spec?.settable
                    ? null
                    : html`<${Badge} title="not marked writable in JK's register map">read only<//>`}
                </td>
              </tr>`;
            })}
          </tbody>
        </table>
      </div>
    <//>

    <${RegisterCard}
      title="Dry contacts and the buzzer"
      help=${{
        summary: 'What each output reacts to, and at what value.',
        body: `Each output has a trigger source — a condition from JK's own list — and the
          value at which it engages and releases. The trigger source numbers are the same
          list the vendor's application shows.`,
      }}
      names=${[
        'lcdBuzzerTrigger',
        'lcdBuzzerTriggerVal',
        'lcdBuzzerReleaseVal',
        'dry1Trigger',
        'dry1TriggerVal',
        'dry1ReleaseVal',
        'dry2Trigger',
        'dry2TriggerVal',
        'dry2ReleaseVal',
      ]}
      registers=${doc.outputs}
      disabled=${readOnly}
      width="wide"
      options=${triggerOptions(doc)}
    />

    <${RegisterCard}
      title="Charging times"
      help=${{
        summary: 'How long the absorption and float stages last.',
        body: `These live in the device-info table rather than with the voltage setpoints,
          which is where JK put them; the voltages they go with are on the Settings tab.`,
      }}
      names=${['rcvTime', 'rfvTime', 'dataStoredPeriod']}
      registers=${doc.outputs}
      disabled=${readOnly}
    />
    </div>
  </div>`;
}

/* The three trigger sources index JK's own list of conditions, which arrives
 * with this document rather than in the register's own description. */
function triggerOptions(doc) {
  const list = (doc.lists.trigger || []).map((t) => [t.id, t.name]);
  return {
    lcdBuzzerTrigger: list,
    dry1Trigger: list,
    dry2Trigger: list,
  };
}
