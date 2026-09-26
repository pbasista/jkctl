/* Firmware: the library, the gate as a checklist, and the flash.
 *
 * The vendor's dialog opens one file at a time and answers with a single
 * sentence: "Minor version must be larger than connected device!" -- which
 * says nothing about whether the model matched, and is why nobody can tell a
 * file for the wrong board from a file that is merely not newer.  Every step
 * of the gate is shown here, passed and failed, with what each compared.
 *
 * The flash is the only irreversible thing on this page.  It asks with the
 * word typed out, it says plainly what an interrupted transfer means, and
 * while it runs it owns the serial port outright -- the live refresh stops,
 * because after the arming write the link is not speaking Modbus any more.
 */

import { Badge, Bar, Card, Confirm, DASH, Empty, Stat } from '/core/js/ui.js';
import { html, useState } from '/core/vendor/preact-htm.module.js';

function Checklist({ checks }) {
  return html`<ul class="list">
    ${checks.map(
      (c) => html`<li key=${c.name}>
        ${c.ok
          ? html`<${Badge} tone="good">ok<//>`
          : c.waived
            ? html`<${Badge} tone="warn">waived<//>`
            : html`<${Badge} tone="bad">no<//>`}
        <span>
          <b>${c.name}</b>
          <br /><span class="muted">${c.detail}</span>
        </span>
      </li>`,
    )}
  </ul>`;
}

export function FirmwareTab({
  unitId,
  library,
  libraryError,
  onLibrary,
  onCheck,
  onFlash,
  job,
  busy,
  readOnly,
}) {
  const [checked, setChecked] = useState(null);
  const [file, setFile] = useState(null);
  const [force, setForce] = useState(false);
  const [asking, setAsking] = useState(false);
  const [dir, setDir] = useState('');

  const pick = async (event) => {
    const chosen = event.target.files?.[0];
    if (!chosen) return;
    const bytes = new Uint8Array(await chosen.arrayBuffer());
    setFile({ name: chosen.name, bytes });
    setChecked(null);
    try {
      setChecked(await onCheck(bytes));
    } catch (err) {
      setChecked({ error: err.message });
    }
  };

  const running = job && (job.state === 'queued' || job.state === 'running');

  return html`<div class="grid">
    <${Card}
      title="Check a file"
      width="wide"
      help=${{
        summary: 'Every step of the gate JK\'s own application runs, all of them.',
        body: html`The file is decrypted and parsed here without touching the battery; only
          the flash below writes anything. <b>Force</b> waives the two checks the vendor's
          "Force Updating" waives — the expiry window and the minor version. The model and
          the major version are never waived, here or there.`,
      }}
    >
      <div class="wrap">
        <input type="file" accept=".jkbms" onChange=${pick} />
        ${file ? html`<span class="muted mono">${file.name}</span>` : null}
      </div>

      ${checked?.error ? html`<div class="notice error" style="margin-top:12px">${checked.error}</div>` : null}

      ${checked && !checked.error
        ? html`<div style="margin-top:14px">
            <div class="stats">
              <${Stat} k="model">${checked.firmware.model}<//>
              <${Stat} k="version">${checked.firmware.version}<//>
              <${Stat} k="built">${checked.firmware.built}<//>
              <${Stat} k="blocks">${checked.firmware.blocks}<//>
            </div>
            <div class="muted" style="font-size:var(--fs-xs);margin-bottom:10px">
              connected unit: ${checked.unit.model} v${checked.unit.version} ·
              image ${checked.firmware.imageBytes} bytes ·
              vector table SP ${checked.firmware.sp} RESET ${checked.firmware.reset}
            </div>
            <${Checklist} checks=${force ? checked.checks.map(waive) : checked.checks} />
            ${!checked.compatible && checked.forcible
              ? html`<label class="toggle" style="margin-top:10px">
                  <input type="checkbox" checked=${force} onChange=${(e) => setForce(e.target.checked)} />
                  <span>Force: waive the expiry and minor-version checks</span>
                </label>`
              : null}
          </div>`
        : null}
    <//>

    <${Card}
      title="Flash"
      badge=${running ? html`<${Badge} tone="warn">in progress<//>` : null}
      help=${{
        summary: 'What this does, and what it costs if it stops halfway.',
        body: html`The arming write puts the board into its bootloader and the link becomes
          a raw XMODEM stream; nothing else may use the port until the transfer ends, so the
          live refresh stops for the duration. An interrupted transfer leaves the unit in
          its bootloader with no application to run. <b>This path has never been exercised
          against a real BMS</b> — the sender matches a capture of the vendor's application
          byte for byte, but the receiving side is a bootloader that ships in no firmware
          file and could not be read.`,
      }}
    >
      <div class="notice" style="margin-bottom:12px">
        <p style="margin:0 0 8px">
          The image sent is <b>exactly the vendor's</b> — the file is decoded the way JK's
          application decodes it, and its bytes match a vendor image to the SHA-256. The risk
          is not in what is sent; it is in what the BMS does with it.
        </p>
        <p class="caveat" style="margin:0">
          <span>!</span>
          <span>
            The BMS is <b>not known to verify</b> what it receives: the file carries no
            signature and no whole-image checksum, JK's application checks only the model,
            version and expiry, and the receiving bootloader ships in no firmware file, so it
            could never be read. On the wire the only guard is XMODEM's weak 8-bit per-block
            checksum. Assume a wrong or corrupt image can be written and run. A failed or
            interrupted flash leaves the unit with no working application; re-arming the
            upgrade over RS485 is done by the application, so a dead app cannot be reflashed
            over the wire. Recovery is then a hardware bench job — the STM32 ROM bootloader
            (BOOT0 high, a USB-TTL adapter, stm32flash), which can rewrite the application
            from the image jkctl extracts if the MCU's readout protection is off. If it is
            on, clearing it mass-erases the JK bootloader too, and recovery then needs a
            full image this tool does not have.
          </span>
        </p>
      </div>
      ${running
        ? html`<div>
            <${Bar} fraction=${job.progress} label=${job.message || job.state} />
            <p class="caveat" style="margin-top:10px">
              <span>!</span><span>Do not close this page or unplug anything.</span>
            </p>
          </div>`
        : job && job.state === 'failed'
          ? html`<div class="notice error">${job.error}</div>`
          : job && job.state === 'done'
            ? html`<div class="notice">
                Sent. The unit restarts into the new application; check the Dashboard.
              </div>`
            : null}

      ${!running
        ? html`<button
            class="btn danger"
            style="margin-top:10px"
            disabled=${readOnly || busy || !file || !checked || checked.error || (!checked.compatible && !force)}
            onClick=${() => setAsking(true)}
          >
            Flash BMS ${unitId}
          </button>`
        : null}
      ${readOnly ? html`<div class="muted" style="margin-top:8px">read-only: flashing is disabled</div>` : null}
    <//>

    <${Card}
      title="Library"
      width="full"
      help=${{
        summary: 'Every .jkbms in a directory, judged against this unit.',
        body: html`JK ships firmware as a tree of one directory per hardware version, each
          holding one file per model. Point this at that tree and it says which of them this
          board would take, rather than making you open them one at a time to find out.`,
      }}
      foot=${html`<div class="wrap">
        <input
          type="text"
          placeholder="/path/to/firmware"
          value=${dir}
          onInput=${(e) => setDir(e.target.value)}
          style="min-width:20rem"
        />
        <button class="btn" disabled=${busy} onClick=${() => onLibrary(dir)}>Read it</button>
      </div>`}
    >
      ${libraryError ? html`<div class="notice error">${libraryError}</div>` : null}
      ${!library?.files?.length
        ? html`<${Empty}>No directory read yet.<//>`
        : html`<div class="table-wrap tall">
            <table>
              <thead>
                <tr>
                  <th>File</th>
                  <th>Model</th>
                  <th>Version</th>
                  <th>Built</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                ${library.files.map(
                  (f) => html`<tr key=${f.path}>
                    <td class="mono">${f.path.replace(library.dir, '').replace(/^\//, '')}</td>
                    <td>${f.model || DASH}</td>
                    <td>${f.version || DASH}</td>
                    <td class="muted">${f.built || DASH}</td>
                    <td>
                      ${f.error
                        ? html`<${Badge} tone="bad" title=${f.error}>unreadable<//>`
                        : f.compatible
                          ? html`<${Badge} tone="good">would flash<//>`
                          : html`<${Badge}
                              title=${(f.checks || []).filter((c) => c.blocking).map((c) => c.detail).join('\n')}
                            >
                              ${(f.checks || []).filter((c) => c.blocking).map((c) => c.name)[0] || 'no'}
                            <//>`}
                    </td>
                  </tr>`,
                )}
              </tbody>
            </table>
          </div>`}
    <//>

    ${asking
      ? html`<${Confirm}
          title=${`Flash ${checked.firmware.model} v${checked.firmware.version} onto BMS ${unitId}?`}
          danger=${true}
          typed="flash"
          confirmLabel="Start the transfer"
          onCancel=${() => setAsking(false)}
          onConfirm=${async () => {
            setAsking(false);
            await onFlash(file.bytes, force);
          }}
          body=${html`<div>
            <p>
              The unit is currently running <b>${checked.unit.model} v${checked.unit.version}</b>.
            </p>
            <p class="caveat">
              <span>!</span>
              <span>
                Flashing is irreversible, and an interrupted transfer leaves the unit in its
                bootloader with no application. The BMS is not known to verify what it
                receives, and this transfer path has never been run against a real BMS. Do
                not do this to a battery you cannot afford to lose.
              </span>
            </p>
            ${force ? html`<p class="caveat"><span>!</span><span>Force is on: the expiry and minor-version checks are waived.</span></p>` : null}
          </div>`}
        />`
      : null}
  </div>`;
}

/* What the checklist looks like with Force on: the two waivable steps become
 * waived rather than failed, which is exactly what the server will do. */
function waive(check) {
  if (check.ok) return check;
  const waivable = check.kind === 'expiry' || check.name === 'minor version is newer';
  return waivable ? { ...check, waived: true, blocking: false } : check;
}
