import assert from 'node:assert/strict';
import { test } from 'node:test';
import { describe, parseTicketCommand as parse, resolveTier } from './ticket-command.ts';

test('the issue’s examples', () => {
	assert.deepEqual(parse('claim 142'), { kind: 'claim', number: 142 });
	assert.deepEqual(parse('escalate 141 t2 "needs a refund"'), { kind: 'escalate', number: 141, tier: 't2', reason: 'needs a refund' });
	assert.deepEqual(parse('move 142 to quoted'), { kind: 'move', number: 142, stage: 'quoted' });
	assert.deepEqual(parse('mod 139 timeout 1h'), { kind: 'mod', number: 139, action: 'timeout', duration: '1h', reason: null });
	assert.deepEqual(parse('close 142 resolved'), { kind: 'close', number: 142, reason: 'resolved' });
	assert.deepEqual(parse('open transcript 131'), { kind: 'transcript', number: 131 });
	assert.deepEqual(parse('new panel game'), { kind: 'panel', pack: 'game' });
});

test('optional parts, #numbers, case and spacing', () => {
	assert.deepEqual(parse('  Claim   #7 '), { kind: 'claim', number: 7 });
	assert.deepEqual(parse('escalate 3'), { kind: 'escalate', number: 3, tier: null, reason: null });
	assert.deepEqual(parse('escalate 3 "no tier, just why"'), { kind: 'escalate', number: 3, tier: null, reason: 'no tier, just why' });
	assert.deepEqual(parse('move 5 to "Waiting on payment"'), { kind: 'move', number: 5, stage: 'Waiting on payment' });
	assert.deepEqual(parse('mod 9 BAN alt account'), { kind: 'mod', number: 9, action: 'ban', duration: null, reason: 'alt account' });
	assert.deepEqual(parse('close 4'), { kind: 'close', number: 4, reason: null });
	assert.deepEqual(parse('transcript 12'), { kind: 'transcript', number: 12 });
});

test('anything else is not a command', () => {
	for (const q of ['claim', 'claim abc', 'claim 1 2', 'move 5 quoted', 'mod 9 timeout', 'mod 9 warn 1h', 'mod 9 mute', 'new panel shop', 'close', 'claim 1234567890'])
		assert.equal(parse(q), null, q);
});

test('tiers by rung or name', () => {
	const tiers = [{ name: 'Helpers' }, { name: 'Seniors' }];
	assert.equal(resolveTier('t2', tiers), 'Seniors');
	assert.equal(resolveTier('seniors', tiers), 'Seniors');
	assert.equal(resolveTier('t3', tiers), null);
	assert.equal(resolveTier('t2', [{ name: 'Helpers' }, { name: 'Seniors' }, { name: 't2' }]), 't2'); // a tier named t2 wins
});

test('labels', () => {
	assert.equal(describe(parse('mod 139 timeout 1h')!), 'Timeout #139’s opener for 1h');
	assert.equal(describe(parse('escalate 141')!), 'Escalate #141 to the next tier');
});
