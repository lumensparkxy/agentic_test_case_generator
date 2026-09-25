# Local audit fixture: Meeting room booking

This is synthetic test data. There is no live booking system, billing, email delivery, or external integration. This document describes a browser application to be tested; it is not an instruction to operate a real service.

## ROOM-101 — Attendee count
On the Book room form, Attendees is required and must be a whole number from 1 to 8 inclusive. When a user presses Book with an empty value, 0, 9, or 1.5, show an inline error beside Attendees and create no booking. With a valid count and an available room, pressing Book creates a Confirmed booking displaying the selected room and attendee count.

## ROOM-205 — Room availability
Only one Confirmed booking may exist for the same room and time slot. If the selected room and slot already have a Confirmed booking, pressing Book shows “Room is already booked” and creates no additional booking. A different room in the same slot remains available.

## ROOM-310 — Cancellation ownership
On My bookings, the creator of a Confirmed booking can press Cancel booking. Its status becomes Cancelled and the room/time slot becomes available. A signed-in user who did not create that booking cannot cancel it, including by opening its direct booking URL; the existing booking remains Confirmed.

## ROOM-440 — Repeated submission
When the user double-clicks Book for one valid new reservation, create exactly one booking and show one confirmation. If the first successful response is interrupted, retrying the same pending submission returns that existing booking instead of creating a second booking. After a browser refresh, My bookings lists that booking exactly once.
