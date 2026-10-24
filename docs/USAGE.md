# Using Alps

## Add and open apps

**Add app** saves a name, web address, and optional image path on this computer.
An address can include a port and path. Without a scheme, it uses HTTP.
Tap a tile to open it in this tab, or its outlined arrow to open a new tab.

Alps opens websites; it does not start their servers or share its login with them.
For a website that only listens on this computer, use [Local apps through Alps](CLI.md).
That adds a tile and a separate HTTPS port protected by your Alps login. Only
expose apps you trust: exposed apps share Alps's hostname, so browsers treat them
as the same site.

## Edit and arrange

Choose **Edit** next to **Your apps** to show the move handles and card pencils;
choose **Stop editing** when done. Use a card's pencil to edit or remove it.

Drag apps to any spot on the grid; use the move handle on touchscreens, or focus
it and use arrow keys. Small and large tiles toggle in edit mode. Screens too
narrow for your arrangement show the apps left to right in the same order.
New apps take the next free spot.

## Icons and address suggestions

Alps looks for a favicon when you leave the address field; an explicit icon wins.
`~/Pictures/icon.svg` works. Address completion stops at your IP, then offers
listening web ports, highest first.

## Sign-ins and remembered devices

Sign-ins last until 90 days without use and survive restarts. Sign out to remember
or forget this device; a remembered one can be forgotten from the sign-in form.
`alps signout-all` or a new password signs out and forgets every device.
Remembered devices get 10 tries a day; others share 5 a day (restart Alps to reset).
Each address signs in separately.

## Install as an app

Use Safari's **Add to Home Screen**, or **Install** on Android and desktop.
Alps opens exposed apps in tabs under a slim bar: **+** brings Alps back, and the
arrow sends the app in front to the browser.

Other apps, and apps that keep reloading in a tab, open in the browser and show a
**Safari** (or **Browser**) label; tap a label with × to try tabs again. Tabs reopen
at each app's start page and stay on that device. In tabs, apps can't use the
camera, microphone, or location; use the arrow for those.
