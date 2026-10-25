import assert from 'node:assert/strict';
import { test } from 'node:test';
import { score } from './fuzzy.ts';

test('substring at a word start beats a subsequence', () => {
	assert.ok(score('serv', 'All servers') > score('srvs', 'All servers'));
	assert.ok(score('srvs', 'All servers') > 0);
});

test('every token has to match', () => {
	assert.equal(score('lumen zzz', 'Lumen Collective'), 0);
	assert.ok(score('lumen col', 'Lumen Collective') > 0);
});

test('shorter titles win ties', () => {
	assert.ok(score('log', 'Log out') > score('log', 'Log out of every device'));
});

test('empty query matches everything', () => {
	assert.equal(score('', 'anything'), 1);
});
