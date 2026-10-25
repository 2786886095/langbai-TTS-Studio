'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { AppUpdater } = require('electron-updater/out/AppUpdater');
const { configureUpdateChannel } = require('../electron/update-policy.cjs');

for (const channel of ['stable', 'beta']) {
  test(`${channel} channel never offers an older installed version`, async () => {
    const updater = new AppUpdater(null, { version: '1.3.0', isPackaged: true });
    updater._isUserWithinRollout = async () => true;
    configureUpdateChannel(updater, channel);
    assert.equal(await updater.isUpdateAvailable({ version: '1.2.12' }), false);
    assert.equal(await updater.isUpdateAvailable({ version: '1.3.0' }), false);
    assert.equal(await updater.isUpdateAvailable({ version: '1.3.1' }), true);
  });
}
