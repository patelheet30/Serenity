/** A role from GET /guilds/{id}/roles. */
export type Role = { id: string; name: string; color: string | null; position: number; managed: boolean };
