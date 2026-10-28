<script lang="ts">
	import { api, send } from '../../api';
	import { save } from '../../autosave.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Icon from '../../components/Icon.svelte';
	import InlineEdit from '../../components/InlineEdit.svelte';
	import Select from '../../components/Select.svelte';
	import Toggle from '../../components/Toggle.svelte';
	import { toast } from '../../toast.svelte';
	import type { TicketPageProps } from '../Tickets.svelte';
	import type { Role } from './settings';

	type Tier = {
		id: number;
		name: string;
		position: number;
		role_ids: string[];
		can_summon: number[];
		requires_reason: boolean;
		reping_minutes: number | null;
	};
	type Ladder = { id: number; name: string; emoji: string | null; tiers: Tier[] };

	let { guildId, onerror }: TicketPageProps = $props();

	let ladders = $state<Ladder[] | null>(null);
	let roles: Role[] = $state([]);
	let typeId: string | null = $state(null);
	let newName = $state('');
	let newRole: string | null = $state(null);

	$effect(() => {
		const id = guildId;
		let stale = false;
		ladders = null;
		Promise.all([api<Ladder[]>(`/guilds/${id}/tickets/tiers`), api<Role[]>(`/guilds/${id}/roles`)]).then(
			([l, r]) => {
				if (stale) return;
				ladders = l;
				roles = r.filter((x) => !x.managed && x.id !== id);
				typeId = l[0] ? String(l[0].id) : null;
			},
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	const ladder = $derived(ladders?.find((l) => String(l.id) === typeId));
	const roleName = (id: string) => roles.find((r) => r.id === id)?.name ?? id;

	async function write(method: string, path: string, body?: unknown) {
		const r = await save(() => send<Ladder[]>(method, `/guilds/${guildId}/tickets${path}`, body));
		if (r) ladders = r;
		return r;
	}
	const patch = (t: Tier, body: Partial<Tier>) => write('PATCH', `/tiers/${t.id}`, body);

	function move(i: number, by: number) {
		const ids = ladder!.tiers.map((t) => t.id);
		[ids[i], ids[i + by]] = [ids[i + by], ids[i]];
		write('PUT', `/types/${ladder!.id}/tiers/order`, { tier_ids: ids });
	}

	async function remove(t: Tier) {
		if (!(await write('DELETE', `/tiers/${t.id}`))) return;
		toast(`${t.name} removed. Its tickets go back to the lowest tier.`);
	}

	async function add(e: SubmitEvent) {
		e.preventDefault();
		if (!ladder || !newName.trim() || !newRole) return;
		if (await write('POST', `/types/${ladder.id}/tiers`, { name: newName.trim(), role_ids: [newRole] })) {
			newName = '';
			newRole = null;
		}
	}

	function setReping(t: Tier, input: HTMLInputElement) {
		const v = Number(input.value || 0);
		if (v !== (t.reping_minutes ?? 0)) patch(t, { reping_minutes: Number.isInteger(v) ? v : NaN }).then((r) => !r && (input.value = String(t.reping_minutes ?? '')));
	}

	const toggleSummon = (t: Tier, other: Tier) =>
		patch(t, { can_summon: t.can_summon.includes(other.id) ? t.can_summon.filter((x) => x !== other.id) : [...t.can_summon, other.id] });
</script>

{#if !ladders}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else if !ladders.length}
	<section class="pane"><EmptyState>No ticket types yet. Create one on the Types page, then give it tiers here.</EmptyState></section>
{:else}
	<section class="pane" aria-labelledby="tlT">
		<div class="ph">
			<h2 id="tlT">Escalation tiers</h2>
			<span class="cnt">{ladder?.tiers.length ?? 0}</span>
			<span class="sp"></span>
			<Select
				label="Ticket type"
				align="right"
				bind:value={typeId}
				options={ladders.map((l) => ({ value: String(l.id), label: `${l.emoji ?? ''} ${l.name}`.trim() }))}
			/>
		</div>
		<p class="pb hint" style="padding-bottom:0">
			Tickets start on the lowest tier; staff escalate them up the ladder, or straight to a tier the current one can summon.
		</p>
		{#if ladder}
			<div class="scroll">
				<table class="tbl">
					<thead>
						<tr><th>Rung</th><th>Tier</th><th>Roles</th><th>Can summon</th><th>Needs a reason</th><th>Re-ping</th><th><span class="sr">Remove</span></th></tr>
					</thead>
					<tbody>
						{#each ladder.tiers as t, i (t.id)}
							<tr>
								<td>
									<div class="rowline" style="justify-content:flex-start;gap:2px">
										<span class="mono dim" style="width:18px">{i + 1}</span>
										<button class="btn sm ghost" aria-label="Move {t.name} down a rung" disabled={i === 0} onclick={() => move(i, -1)}>↓</button>
										<button class="btn sm ghost" aria-label="Move {t.name} up a rung" disabled={i === ladder.tiers.length - 1} onclick={() => move(i, 1)}>↑</button>
									</div>
								</td>
								<td><InlineEdit label="name of {t.name}" value={t.name} onsave={(v) => v.trim() && patch(t, { name: v.trim() })} /></td>
								<td style="white-space:normal">
									<div style="display:flex;flex-wrap:wrap;gap:4px;align-items:center">
										{#each t.role_ids as r (r)}
											<button class="chip" aria-label="Remove @{roleName(r)} from {t.name}" onclick={() => patch(t, { role_ids: t.role_ids.filter((x) => x !== r) })}>@{roleName(r)} <Icon name="x" style="width:10px;height:10px" /></button>
										{/each}
										<!-- Native, so the table's scroll box doesn't clip it. -->
										<select
											class="inp"
											style="width:auto;height:26px"
											aria-label="Add a role to {t.name}"
											onchange={(e) => {
												const id = e.currentTarget.value;
												e.currentTarget.value = '';
												if (id) patch(t, { role_ids: [...t.role_ids, id] });
											}}
										>
											<option value="">+ role</option>
											{#each roles.filter((r) => !t.role_ids.includes(r.id)) as r (r.id)}<option value={r.id}>@{r.name}</option>{/each}
										</select>
									</div>
								</td>
								<td style="white-space:normal">
									<div style="display:flex;flex-wrap:wrap;gap:4px">
										{#each ladder.tiers.filter((o) => o.id !== t.id) as o (o.id)}
											<button class="chip" aria-pressed={t.can_summon.includes(o.id)} aria-label="{t.name} can summon {o.name}" onclick={() => toggleSummon(t, o)}>{o.name}</button>
										{:else}
											<span class="dim">-</span>
										{/each}
									</div>
								</td>
								<td><Toggle label="Escalating to {t.name} needs a reason" bind:checked={t.requires_reason} onchange={async (on) => !(await patch(t, { requires_reason: on })) && (t.requires_reason = !on)} /></td>
								<td>
									<div class="rowline" style="justify-content:flex-start;gap:6px">
										<input class="inp" style="width:72px" type="number" min="0" max="1440" inputmode="numeric" aria-label="Re-ping {t.name} after minutes, 0 or blank for never" placeholder="never" value={t.reping_minutes ?? ''} onchange={(e) => setReping(t, e.currentTarget)} />
										<span class="hint">min</span>
									</div>
								</td>
								<td class="r"><button class="btn sm ghost" aria-label="Remove {t.name}" onclick={() => remove(t)}><Icon name="x" /></button></td>
							</tr>
						{:else}
							<tr><td colspan="7"><EmptyState>No tiers, so these tickets can’t be escalated.</EmptyState></td></tr>
						{/each}
					</tbody>
				</table>
			</div>
			<form class="addrow" onsubmit={add}>
				<label class="field" style="flex:1;min-width:140px">
					<span>New top tier</span>
					<input class="inp" maxlength="50" placeholder="e.g. Senior staff" bind:value={newName} />
				</label>
				<div class="field">
					<span>Role</span>
					<Select label="Role for the new tier" placeholder="Choose role…" bind:value={newRole} options={roles.map((r) => ({ value: r.id, label: `@${r.name}` }))} />
				</div>
				<button class="btn pri" type="submit" disabled={!newName.trim() || !newRole}><Icon name="plus" />Add tier</button>
			</form>
		{/if}
	</section>
{/if}
