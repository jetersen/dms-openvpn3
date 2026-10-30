// Exercise the service's actual QML JavaScript without opening a browser or VPN.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const qml = fs.readFileSync(path.join(__dirname, '../OpenVpn3Service.qml'), 'utf8');

function harness(browserWorks = true) {
    const opened = [], notices = [];
    const root = {
        authenticationPath: '', authentication: {fields: [], url: ''},
        authenticationInput: '', authenticationBrowserOpened: false,
        lastBrowserAuthKey: '', connectionPath: '', connectionMissingCount: 0,
        actionInFlight: false, actionPath: '', actionKind: '', lastError: '',
        helperPath: '/helper.py', sessions: []
    };
    const context = vm.createContext({root,
        Qt: {openUrlExternally(url) { opened.push(url); return browserWorks; }},
        ToastService: {showInfo(...args) {notices.push(args);}, showError(...args) {notices.push(args);}},
        actionProcess: {running: false}, actionTimeout: {restart() {}}
    });
    const names = ['connectProfile', 'disconnectSession', 'requestAuthentication',
        'followConnection', 'openAuthenticationBrowser', 'applyAuthentication', 'clearAuthentication'];
    for (const name of names) {
        const match = qml.match(new RegExp('function ' + name + '\\(([^)]*)\\) \\{'));
        const start = qml.indexOf(match[0]) + match[0].length;
        let depth = 1, end = start;
        while (depth) {
            const c = qml[end++];
            if (c === '{') depth++;
            if (c === '}') depth--;
        }
        root[name] = vm.runInContext('(function(' + match[1] + ') {' + qml.slice(start, end - 1) + '})', context);
    }
    for (const key of Object.keys(root)) {
        Object.defineProperty(context, key, {get: () => root[key], set: value => {root[key] = value;}});
    }
    return {root, opened, notices};
}

const url = 'https://login.example.test/session-one';
const browserAuth = {fields: [], url};
const session = {path: '/session/one', config_name: 'Work', state: 'auth_required', active: false};

test('explicit sign-in opens the browser once; refresh does not create another tab', () => {
    const {root, opened} = harness();
    root.requestAuthentication(session.path);
    root.applyAuthentication(browserAuth);
    assert.deepEqual(opened, [url]);
    assert.equal(root.authenticationBrowserOpened, true);
    root.actionInFlight = false;
    root.requestAuthentication(session.path);
    root.applyAuthentication(browserAuth);
    assert.deepEqual(opened, [url]);
    root.openAuthenticationBrowser();
    assert.deepEqual(opened, [url, url]);
});

test('a later challenge opens its new URL once', () => {
    const {root, opened} = harness();
    root.authenticationPath = session.path;
    root.applyAuthentication(browserAuth);
    const next = {fields: [], url: url + '/next'};
    root.applyAuthentication(next);
    root.applyAuthentication(next);
    assert.deepEqual(opened, [url, next.url]);
});

test('late authentication after Connect needs no second user click', () => {
    const {root, opened} = harness();
    root.connectProfile('/config/one');
    root.actionInFlight = false;
    root.connectionPath = session.path;
    root.sessions = [{...session, state: 'connecting'}];
    root.followConnection();
    assert.equal(root.actionInFlight, false);
    root.sessions = [session];
    root.followConnection();
    assert.equal(root.actionKind, 'auth-request');
    assert.equal(root.authenticationPath, session.path);
    root.applyAuthentication(browserAuth);
    assert.deepEqual(opened, [url]);
});

test('background authentication does not open tabs or fetch credentials', () => {
    const {root, opened} = harness();
    root.sessions = [session];
    root.followConnection();
    assert.deepEqual(opened, []);
    assert.equal(root.actionInFlight, false);
    assert.equal(root.authenticationPath, '');
});

test('browser failure allows an explicit retry without an automatic retry loop', () => {
    const {root, opened} = harness(false);
    root.authenticationPath = session.path;
    root.applyAuthentication(browserAuth);
    root.applyAuthentication(browserAuth);
    assert.deepEqual(opened, [url]);
    assert.equal(root.authenticationBrowserOpened, false);
    assert.match(root.lastError, /Could not open/);
    root.openAuthenticationBrowser();
    assert.equal(opened.length, 2);
});

test('a new connection can open the same URL again', () => {
    const {root, opened} = harness();
    root.authenticationPath = session.path;
    root.applyAuthentication(browserAuth);
    root.connectProfile('/config/one');
    root.actionInFlight = false;
    root.requestAuthentication(session.path);
    root.applyAuthentication(browserAuth);
    assert.deepEqual(opened, [url, url]);
});

test('successful connection clears sign-in state and announces success once', () => {
    const {root, notices} = harness();
    root.authenticationPath = root.connectionPath = session.path;
    root.applyAuthentication(browserAuth);
    root.sessions = [{...session, state: 'connected', active: true}];
    root.followConnection();
    root.followConnection();
    assert.equal(root.connectionPath, '');
    assert.equal(root.authenticationPath, '');
    assert.equal(root.authentication.url, '');
    assert.deepEqual(notices, [['Profiles', 'Connected to Work']]);
});

test('cancelled or removed connections stop following the session', () => {
    const {root} = harness();
    root.authenticationPath = root.connectionPath = session.path;
    root.disconnectSession(session.path);
    assert.equal(root.connectionPath, '');
    assert.equal(root.authenticationPath, '');
    root.actionInFlight = false;
    root.connectionPath = session.path;
    root.followConnection();
    assert.equal(root.connectionPath, session.path); // One stale snapshot is tolerated.
    root.followConnection();
    assert.equal(root.connectionPath, '');
});
