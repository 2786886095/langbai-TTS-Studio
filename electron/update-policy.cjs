'use strict';

function configureUpdateChannel(updater, requestedChannel) {
  updater.allowPrerelease = requestedChannel === 'beta';
  updater.channel = requestedChannel === 'beta' ? 'beta' : 'latest';
  // electron-updater's channel setter enables downgrades, including old stable releases.
  updater.allowDowngrade = false;
}

module.exports = { configureUpdateChannel };
