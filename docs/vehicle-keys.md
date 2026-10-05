# Assigning a License Plate to a Vehicle Key

[Deutsche Version](vehicle-keys.de.md)

This guide explains how a vehicle key (green or yellow user key) gets a license plate on a PIUSI dispenser with MC controller. The plate then appears in every transaction that FluidTrack-Mini reads from the master key.

## Key Facts

- **The key does not store a plate.** A vehicle key has no writable memory. It only contains a fixed serial number (1-Wire family `0x81`). Memory read commands return nothing but `0xFF`.
- **The plate lives in the dispenser.** The dispenser keeps a user list. Each entry has a name of 1 to 10 characters, a user number from 1 to 50 and an optional key. Enter the plate as the name, for example `AB.CD.123`.
- **The master key only transfers transactions to the PC.** Users and plates cannot be sent to the dispenser with it. That is why FluidTrack-Mini cannot write them either.
- **The number in the transactions** is most likely the user number. FluidTrack-Mini stores it as "operator".

## Assigning a Key

At the dispenser, menu "USERS / ADD":

1. Open the menu with the master key or the manager PIN. The factory PIN is `1234`.
2. Use the arrow keys to go to **USERS** and press **ENTER**. Then select **USERS ADD** and press **ENTER**.
3. At **USER NAME**, enter the license plate, at most 10 characters.
4. Set **USER PIN** to **NO**.
5. Set **ELECTRONIC KEY** to **YES**.
6. At "TOUCH USER KEY", hold the vehicle key against the reader.
   - **Green key holder:** the dispenser also asks for the four-digit **KEY CODE** printed on the holder.
   - **Yellow key holder:** no code needed, the key is recognized automatically.
7. Leave **USER NUMBER** at **AUTO** and confirm with **ENTER**.

The dispenser then briefly shows all data of the new user.

## Changing a Plate

An existing user cannot be edited.

1. Delete the user by user number under **USERS / DELETE**.
2. Create it again with the correct plate as described above.

After deleting, the key is free and can be assigned again. **USERS / VIEW** lists all users with number, name and an asterisk for assigned keys.

## Messages

| Message | Meaning |
|---|---|
| `WARNING KEY ALREADY ASSIGNED` | The key belongs to another user. Delete that user first. |
| `WARNING NAME ALREADY ASSIGNED` | A user with this name already exists. |
| `UNKNOWN USER KEY` | When refueling: the key is not assigned to any user. |

## Alternative: Asking for the Plate at Every Refueling

Under **SYSTEM CONFIGURATION** (in the SYSTEM menu, press `#` and `1` together), set **REGISTRATION NUMBER** to **ENABLED**. The dispenser then asks for a plate of up to 10 characters at every refueling.

## Limitations

- This guide follows the manual of the MC-BOX controller. Menu names may differ slightly depending on your dispenser's firmware.
- Whether newer versions of the PIUSI SelfService software can transfer users over an RS-485 cable has not been verified.

## Source

- PIUSI, *MC-BOX Management System Software – Use Manual*, Bulletin M0187 EN Rev. 0, sections 2.2, 3.4, 4.5.1, 4.6.2 and 4.6.6 ([PDF](https://www.oilybits.com/downloads/Piusi_MC_Software_Instruction_Manual.pdf))
