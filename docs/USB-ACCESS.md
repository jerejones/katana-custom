# Linux USB access: narrowly scoped, operator-controlled

No repository program changes permissions, resets USB, sets configuration or
automatically detaches a kernel driver. The Go runtime and flasher refuse a bound
interface. Permission approval and interface unbinding are separate decisions.
Do not run the flasher as root; it refuses root intentionally.

## Identify before changing anything

Use `lsusb` and `/sys/bus/usb/devices/` to identify the original `041e:3247` and
read its `serial`, `busnum`, `devnum`, `idVendor`, `idProduct`, `bcdDevice` files.
Pass the exact serial to the tool; do not assume a bus address remains stable.
USB device nodes are `/dev/bus/usb/BBB/DDD`. Read-only sysfs discovery is not a
USB exchange, but device descriptor reads/claims are live interaction.

If permission is denied, arrange read/write access for your normal user to only
that node. An administrator can review a udev rule like this (replace the serial;
this file is an example, not automatically installed):

```
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="041e", ATTR{idProduct}=="3247", ATTR{serial}=="YOUR_DEVICE_SERIAL", TAG+="uaccess"
```

`uaccess` is for an active local login seat; it may not grant a headless service
access. A dedicated restricted group plus mode `0660`, or a targeted user ACL,
is another administrator-reviewed option. Do not use `0666`, broad USB wildcards
or chmod the entire bus. A udev rule alone does **not** release a claimed HID
interface. Do not reset/replug during an uncertain flash just to apply a rule.

## Interface binding

For clock/text only, inspect the driver symlink for the exact device's interface
**4** (`DEVICE:1.4`). If it is bound to `usbhid`, an operator may explicitly choose
to write that exact interface name to `/sys/bus/usb/drivers/usbhid/unbind`, then
restore it via `bind` after use. Verify parent device identity first. Do not
unbind the whole USB device or audio interfaces. These tools do not automate it.

Maintenance qualification/entry separately needs interface **3** (`DEVICE:1.3`),
which includes consumer controls. Unbinding it may disable those controls while
unbound; require separate opt-in and restore afterward. Interface 4 is used for
the normal version query, not loader-entry commands. Never redirect interface-3
commands onto interface 4 to work around a claim failure.

After entry/reset the normal node may disappear and the loader enumerates as
`041e:30cf` (**SBEVOUpgrader0100** observed). Its node ACL and interface bindings
are new. Review the recorded bus/physical port chain and same-device custody,
then grant only the observed loader node access and release only its matched HID
interface if necessary. The normal serial rule does not authorize arbitrary
loader devices; do not fabricate a loader serial. `--capture-loader` can record
it without repeating entry. After return, recheck the new normal node and HID4
binding before `--verify-return` or clock use.

Access errors are blockers to resolve deliberately, not grounds for retries,
sudo fallback or whole-audio detachment. See SAFETY.md if any persistent command was attempted.
