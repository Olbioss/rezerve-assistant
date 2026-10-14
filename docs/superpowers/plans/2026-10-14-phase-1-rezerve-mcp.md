# Phase 1 — Rezerve's MCP endpoint: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Rezerve an authenticated MCP endpoint (`/api/mcp`). Through it, the rezerve-asistan agent can read a business's services and free slots, and book, move and cancel a chat customer's appointments. Every write goes through the same booking rules the public booking page uses.

**Architecture:** Each booking rule in today's server actions moves into a plain "core" function that returns a result object: create, customer cancel, and a new customer reschedule. The server actions become thin wrappers, and the existing tests pin their behaviour unchanged.

A thin `lib/assistant/` layer sits on top:
- authentication: bearer token plus identity headers, never model-written arguments;
- tool functions returning exactly the shapes declared in zod schemas;
- MCP registration through `mcp-handler`.

A schema migration adds `bookings.source` / `customer_ref` and `business_profiles.assistant_enabled` / `assistant_notes`. An unlisted `eval` business is added for the eval harness.

**Tech Stack:** Next.js 16 (App Router, Cache Components), TypeScript 5.9, Drizzle ORM 0.45 on Postgres, zod 4.4, Vitest, Bun 1.3.1, `mcp-handler` 2.3.0 + `@modelcontextprotocol/server` 2.3.1 (MCP SDK v2, spec 2026-07-28), `@modelcontextprotocol/client` 2.3.1 (tests only).

**Spec:** `/Users/olbios/repos/deploys/rezerve-asistan/docs/superpowers/specs/2026-10-14-rezerve-asistan-design.md`, sections §5, §6.1–6.3, §10 (Rezerve) and the `seed:eval` / `mcp:schema` parts of §6.2 and §10. The widget (§6.4) and the kapora event (§6.5) are later phases.

## Global Constraints

**Where the work happens**
- **Repository and branch:** all work is in `/Users/olbios/repos/deploys/rezerve`, on branch `feat/assistant-mcp`. Nothing is pushed and no PR is opened during execution.
- **Databases (critical):**
  - The `.env` in that repo points `DATABASE_URL` at a **Neon** database. Never run migrations, tests or seed scripts against it.
  - Every command that touches a database is written with the prefix `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test`. This is the local Homebrew Postgres 17.
  - A `DATABASE_URL` given on the command line overrides Bun's `.env` auto-load and `dotenv/config`. This was verified.
  - Other variables (`BETTER_AUTH_SECRET`, `NEXT_PUBLIC_APP_URL`, …) still come from `.env`.
- **Worktrees:** if you work in a git worktree, copy `.env` into it (it is gitignored) and run `bun install` first.
- **Production is not this plan's job.** Applying migration 0014 to Neon and setting `ASSISTANT_MCP_TOKEN` on Vercel happen after merge, by the user.

**Toolchain and style**
- **Package versions, exact:** `mcp-handler@2.3.0` and `@modelcontextprotocol/server@2.3.1` (dependencies); `@modelcontextprotocol/client@2.3.1` (devDependency). zod stays `^4.4.3`. Node is v24.13, Bun 1.3.1.
- **Tests:**
  - Test files live under `lib/**` and are named `*.test.ts`; integration tests are named `*.integration.test.ts` (that is `vitest.config.ts`'s include).
  - Each integration test seeds its own `org_itest_*` organization and deletes it in `afterAll`, then calls `db.$client.end()`.
  - `next/*` and email modules are mocked with `vi.mock`, the way existing tests do.
- **Style:**
  - Biome: 2-space indent, 80 columns, organized imports. Before every commit, run `bun run check:fix` and then `bun run check`.
  - Comments explain *why*, in the repo's voice.
  - Every user-facing message is Turkish. Existing Turkish messages keep their exact text.
- **Commits** use `git commit -m "<subject>" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`.

**The MCP contract**
- **Identity headers, exact names:**
  - `Authorization: Bearer <ASSISTANT_MCP_TOKEN>`, checked fail-closed and in constant time;
  - `X-Rezerve-Business: <slug>`;
  - `X-Rezerve-Customer: <customer_ref>`.
- **Customer ref format, exact:** `^(web|telegram|whatsapp|eval):[a-z2-7]{26}$`. Refs are never trimmed, lowercased or otherwise normalised.
- **Refusals:**
  - missing token → 503 `misconfigured`;
  - wrong or missing bearer → 401;
  - bad customer ref → 400;
  - unknown business, or `assistant_enabled = false` → 403, the same answer for both.
- **Tool names, exact:** `get_business_info`, `list_services`, `find_slots`, `list_my_bookings`, `get_booking`, `create_booking`, `reschedule_booking`, `cancel_booking`. Only the last three write.
- **No tool input** has a business, slug, organization or customer-ref field.
- **Tool error codes** (stable): `not_found`, `invalid`, `range_too_long`, `throttled`, `slot_unavailable`, `slot_taken`, `terms_changed`, `email_required`, `payment_start_failed`, `already_cancelled`, `pending`, `started`, `refund_window_closed`.
- **Fixed booking values:**

  | Rule | Value |
  |---|---|
  | Kapora hold | 30 minutes |
  | Refund notice | 24 hours (`REFUND_NOTICE_HOURS`, unchanged) |
  | `find_slots` range | ≤ 14 days |
  | `find_slots` limit | default 10, max 20 |
  | Assistant throttle | 3 attempts per customer ref per hour, never counted by address |

**Known deviations from the spec:**
- **The route** is `app/api/mcp/route.ts`, not `app/api/mcp/[transport]/route.ts`. `mcp-handler` 2.x serves whatever path it is mounted at; the `[transport]` segment was the 1.x convention.
- **List outputs are wrapped:** `list_services` returns `{ services: [...] }` and `list_my_bookings` returns `{ bookings: [...] }`, not bare arrays. MCP structured content must be a JSON object.
- **Extra output fields:** `create_booking` and `reschedule_booking` also return the booking's `starts_at`/`local_label` (and `previous_local_label`), so the assistant can read the time back without another call.

## Review Focus

1. **An assistant booking without an email still notifies the owner.** Only the customer email is skipped. Tested in Task 4 ("…and still tells the owner").
2. **`starts_at` sent with an offset is the same instant.** `"…T13:00:00+03:00"` books exactly the instant `"…T10:00:00Z"` would. Tested in Task 9.
3. **"Today" is the business's local date.** At 00:30 in Istanbul, while UTC is still the previous day, `get_business_info` says the Istanbul date. Tested in Task 8.
4. **A customer ref spelled any other way is refused.** Uppercase, short, an unknown channel, or a non-base32 character gets a 400 and is never normalised into someone's ref. Tested in Task 7.
5. **A switched-off service is invisible to the assistant.** It is neither listed by `list_services` nor bookable through `create_booking`. Tested in Task 8 (list) and Task 9 (create).

---

### Task 1: Local test database, and the assistant's columns (migration 0014)

**Files:**
- Modify: `lib/db/schema/booking-schema.ts` (enum, two `bookings` columns, index, check; `booking_attempts` email nullable + `customer_ref` + check)
- Modify: `lib/db/schema/business-schema.ts` (`assistantEnabled`, `assistantNotes`)
- Create: `lib/db/migrations/0014_assistant_channel.sql` (generated, plus a hand-written backfill)
- Modify: `lib/actions/owner-bookings.ts` (owner bookings record `source: "owner"`)
- Create: `lib/db/assistant-columns.integration.test.ts`
- Modify: `lib/actions/owner-bookings.integration.test.ts` (one new test)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `bookings.source: "web" | "owner" | "assistant"` (not null, default `"web"`);
  - `bookings.customerRef: string | null`;
  - `bookingAttempts.email: string | null`;
  - `bookingAttempts.customerRef: string | null`;
  - `businessProfiles.assistantEnabled: boolean` (default `false`);
  - `businessProfiles.assistantNotes: string | null`;
  - the exported enum `bookingSource`.

- [ ] **Step 1: Branch, scratch database, green baseline**

```bash
cd /Users/olbios/repos/deploys/rezerve
git switch -c feat/assistant-mcp
createdb -h localhost rezerve_assistant_test
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run db:migrate
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test
```

Expected: the migrations apply (0000–0013) and every existing test file passes.
- If `createdb` reports that the database exists, run `dropdb -h localhost rezerve_assistant_test` first. It is this plan's own scratch database.
- If the baseline is not green, stop and report. Failures found later must be attributable to this work.

- [ ] **Step 2: Write the failing test**

Create `lib/db/assistant-columns.integration.test.ts`:

```ts
/**
 * The columns the chat assistant relies on, and the two rules the database
 * keeps for it whatever the application does: an assistant booking always
 * names its customer, and a throttle row always names someone.
 */
import { eq } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { db } from "@/lib/db";
import { pgErrorCode } from "@/lib/db/errors";
import { organization } from "@/lib/db/schema/auth-schema";
import { bookingAttempts, bookings } from "@/lib/db/schema/booking-schema";
import { businessProfiles } from "@/lib/db/schema/business-schema";
import { services } from "@/lib/db/schema/service-schema";

const ORG = "org_itest_assistant_columns";
const CHECK_VIOLATION = "23514";
const ALI = `telegram:${"a".repeat(26)}`;
const HOUR = 3_600_000;
let serviceId: string;

beforeAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db.insert(organization).values({
    id: ORG,
    name: ORG,
    slug: "itest-assistant-columns",
    createdAt: new Date(),
  });
  await db
    .insert(businessProfiles)
    .values({ organizationId: ORG, timezone: "UTC" });
  const [service] = await db
    .insert(services)
    .values({
      organizationId: ORG,
      name: "Saç Kesimi",
      durationMinutes: 30,
      priceCents: 80_000,
    })
    .returning({ id: services.id });
  serviceId = service.id;
});

afterAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db.$client.end();
});

let n = 0;
/** Each booking an hour after the last, so the overlap constraint stays out. */
function booking(values: Partial<typeof bookings.$inferInsert> = {}) {
  n += 1;
  const startsAt = new Date(Date.now() + (24 + n) * HOUR);
  return db
    .insert(bookings)
    .values({
      organizationId: ORG,
      serviceId,
      customerName: "Ali Yılmaz",
      startsAt,
      endsAt: new Date(startsAt.getTime() + 30 * 60_000),
      ...values,
    })
    .returning();
}

async function violation(write: Promise<unknown>) {
  try {
    await write;
    return undefined;
  } catch (err) {
    return pgErrorCode(err);
  }
}

describe("the assistant's columns", () => {
  it("records a booking as the public form's unless told otherwise", async () => {
    const [row] = await booking({ customerEmail: "ali@test.dev" });
    expect(row.source).toBe("web");
    expect(row.customerRef).toBeNull();
  });

  it("keeps an assistant booking's customer", async () => {
    const [row] = await booking({ source: "assistant", customerRef: ALI });
    expect(row.source).toBe("assistant");
    expect(row.customerRef).toBe(ALI);
  });

  it("refuses an assistant booking that names no customer", async () => {
    expect(await violation(booking({ source: "assistant" }))).toBe(
      CHECK_VIOLATION
    );
  });

  it("refuses a throttle row that names nobody", async () => {
    expect(
      await violation(
        db
          .insert(bookingAttempts)
          .values({ organizationId: ORG, ip: "203.0.113.9" })
      )
    ).toBe(CHECK_VIOLATION);
  });

  it("starts every business with the assistant switched off", async () => {
    const profile = await db.query.businessProfiles.findFirst({
      where: eq(businessProfiles.organizationId, ORG),
    });
    expect(profile?.assistantEnabled).toBe(false);
    expect(profile?.assistantNotes).toBeNull();
  });
});
```

- [ ] **Step 3: Run it to see it fail**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/db/assistant-columns.integration.test.ts`

Expected: FAIL. The columns do not exist yet: `source` is undefined, and the inserts that should be refused succeed.

- [ ] **Step 4: Add the columns to the schema**

In `lib/db/schema/booking-schema.ts`, directly after the `bookingCancelledBy` enum, add:

```ts
/**
 * Where a booking came from: the public booking form, the owner's panel, or
 * the chat assistant on a customer's confirmed request (through /api/mcp).
 */
export const bookingSource = pgEnum("booking_source", [
  "web",
  "owner",
  "assistant",
]);
```

In the `bookings` columns, directly after `cancelledBy: bookingCancelledBy("cancelled_by"),`, add:

```ts
    /** Where the booking came from — see bookingSource. */
    source: bookingSource("source").notNull().default("web"),
    /**
     * The chat customer an assistant booking belongs to: an opaque HMAC the
     * assistant derives from the channel's user id, so no Telegram id or
     * phone number is ever stored here. It is the credential for that
     * customer's later moves and cancellations, the way a booking's own uuid
     * is on its page.
     */
    customerRef: text("customer_ref"),
```

Replace the `bookings` table's extras array with:

```ts
  (table) => [
    index("bookings_org_starts_at_idx").on(
      table.organizationId,
      table.startsAt
    ),
    index("bookings_org_customer_ref_idx").on(
      table.organizationId,
      table.customerRef
    ),
    check("bookings_time_order", sql`${table.endsAt} > ${table.startsAt}`),
    check(
      "bookings_assistant_has_customer_ref",
      sql`${table.source} <> 'assistant' OR ${table.customerRef} IS NOT NULL`
    ),
  ]
```

In `bookingAttempts`, replace `email: text("email").notNull(),` with:

```ts
    /** The form's visitor. Null for an attempt the assistant made. */
    email: text("email"),
    /** The assistant's customer, for attempts made through /api/mcp. */
    customerRef: text("customer_ref"),
```

Replace the `bookingAttempts` extras array with:

```ts
  (table) => [
    index("booking_attempts_org_created_idx").on(
      table.organizationId,
      table.createdAt
    ),
    check(
      "booking_attempts_has_identity",
      sql`${table.email} IS NOT NULL OR ${table.customerRef} IS NOT NULL`
    ),
  ]
```

In `lib/db/schema/business-schema.ts`, add `boolean` to the `drizzle-orm/pg-core` import. Then add these after `description: text("description"),`:

```ts
  /**
   * Whether the chat assistant may book for this business. Off until the
   * business turns it on: /api/mcp answers 403 for a business that has not.
   */
  assistantEnabled: boolean("assistant_enabled").notNull().default(false),
  /**
   * Facts the owner wants the assistant to be able to quote — parking, what
   * to bring, aftercare. The model sees them as data, never as instructions.
   */
  assistantNotes: text("assistant_notes"),
```

- [ ] **Step 5: Generate the migration and add the backfill**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run db:generate --name assistant_channel`

Expected: it creates `lib/db/migrations/0014_assistant_channel.sql` plus a new snapshot and journal entry. Open the SQL file and check that it contains exactly these changes, in drizzle's order and quoting:
- `CREATE TYPE "public"."booking_source" AS ENUM('web', 'owner', 'assistant')`;
- `booking_attempts.email DROP NOT NULL`;
- `ADD COLUMN "customer_ref" text` on both `booking_attempts` and `bookings`;
- `ADD COLUMN "source" "booking_source" DEFAULT 'web' NOT NULL`;
- `ADD COLUMN "assistant_enabled" boolean DEFAULT false NOT NULL` and `"assistant_notes" text`;
- `CREATE INDEX "bookings_org_customer_ref_idx"`;
- the two `CHECK` constraints.

If anything else appears, stop: the snapshot and the schema disagree.

Append exactly this to the end of `0014_assistant_channel.sql`:

```sql
--> statement-breakpoint
-- Nothing recorded where a booking came from before this migration. One thing
-- is certain: the public form always takes an email, so a booking without one
-- was entered by the owner (or seeded as demo history).
UPDATE "bookings" SET "source" = 'owner' WHERE "customer_email" IS NULL;
```

- [ ] **Step 6: Migrate the scratch database and run the test**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run db:migrate
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/db/assistant-columns.integration.test.ts
```

Expected: PASS (5 tests).

- [ ] **Step 7: Owner bookings say they are the owner's — failing test first**

In `lib/actions/owner-bookings.integration.test.ts`, inside `describe("ownerCreateBooking", …)`, directly after the first `it(…)`, add:

```ts
  it("records the booking as the owner's", async () => {
    expect(await book()).toEqual({ ok: true });
    const [row] = await rowsFor("ayse@test.dev");
    expect(row.source).toBe("owner");
  });
```

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/actions/owner-bookings.integration.test.ts`

Expected: FAIL on the new test with `expected 'web' to be 'owner'`.

- [ ] **Step 8: Record the source**

In `lib/actions/owner-bookings.ts`, in `ownerCreateBooking`'s `.values({ … })`, add `source: "owner",` after `status: "confirmed",`.

Run the same command. Expected: PASS.

- [ ] **Step 9: Whole suite, types, lint**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test
bun run typecheck
bun run check:fix && bun run check
```

Expected: every test passes, with no type errors and no lint errors.

- [ ] **Step 10: Commit**

```bash
git add lib/db/schema/booking-schema.ts lib/db/schema/business-schema.ts lib/db/migrations lib/actions/owner-bookings.ts lib/actions/owner-bookings.integration.test.ts lib/db/assistant-columns.integration.test.ts
git commit -m "feat: record where a booking came from, and whose chat it was" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The booking throttle counts a chat customer by their customer ref

**Files:**
- Modify: `lib/booking/throttle.ts`
- Modify: `lib/booking/throttle.integration.test.ts` (one new `describe`)

**Interfaces:**
- Consumes: `bookingAttempts.customerRef` and nullable `bookingAttempts.email` (Task 1).
- Produces:
  ```ts
  export type ThrottleIdentity =
    | { ip: string | null; email: string }
    | { customerRef: string };
  export function checkBookingThrottle(
    input: { organizationId: string } & ThrottleIdentity
  ): Promise<string | null>; // THROTTLED_MESSAGE or null
  ```

- [ ] **Step 1: Write the failing tests**

Append to `lib/booking/throttle.integration.test.ts`. The file's `beforeEach` already recreates both organizations, which clears their attempts.

```ts
describe("checkBookingThrottle — the assistant's customers", () => {
  const ALI = `telegram:${"a".repeat(26)}`;
  const VELI = `telegram:${"b".repeat(26)}`;
  const chat = (customerRef: string) =>
    checkBookingThrottle({ organizationId: ORG, customerRef });

  it("allows a chat customer three attempts an hour, then refuses", async () => {
    expect(await chat(ALI)).toBeNull();
    expect(await chat(ALI)).toBeNull();
    expect(await chat(ALI)).toBeNull();
    expect(await chat(ALI)).toBe(THROTTLED_MESSAGE);
  });

  it("counts each chat customer on their own", async () => {
    for (let i = 0; i < 4; i += 1) await chat(ALI);
    expect(await chat(VELI)).toBeNull();
  });

  it("never buckets chat customers by the assistant's address", async () => {
    // Seven customers through one server, each on their first try — more
    // than the form allows one address.
    for (const letter of "cdefghi") {
      expect(await chat(`telegram:${letter.repeat(26)}`)).toBeNull();
    }
    // And the form's visitors are not charged for the chat's traffic.
    expect(await attempt()).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/booking/throttle.integration.test.ts`

Expected: FAIL. The new tests reject with a not-null violation on `email`, or the throttle never refuses, because `customerRef` is ignored.

- [ ] **Step 3: Implement**

Replace the body of `lib/booking/throttle.ts` from the `const WINDOW_MINUTES` line to the end with the following. Keep the file's existing doc comment and imports.

```ts
const WINDOW_MINUTES = 60;
const MAX_PER_IP = 6;
const MAX_PER_EMAIL = 3;
/** One chat customer, counted like one email: three tries an hour. */
const MAX_PER_CUSTOMER_REF = 3;

export const THROTTLED_MESSAGE =
  "Çok fazla randevu denemesi yaptınız — lütfen bir süre sonra tekrar deneyin.";

/**
 * Who an attempt counts against. The booking page knows an address and an
 * email. The chat assistant calls from its own servers, so an address would
 * put every chat customer in one bucket — its customer ref is the only
 * identity it has, and those attempts are never counted by address.
 */
export type ThrottleIdentity =
  | { ip: string | null; email: string }
  | { customerRef: string };

export async function checkBookingThrottle(
  input: { organizationId: string } & ThrottleIdentity
): Promise<string | null> {
  const { organizationId } = input;
  const since = new Date(Date.now() - WINDOW_MINUTES * 60_000);

  // Nothing older than the window can affect a decision, so the table prunes
  // itself on the way past rather than needing a job of its own.
  await db.delete(bookingAttempts).where(lt(bookingAttempts.createdAt, since));

  const recent = and(
    eq(bookingAttempts.organizationId, organizationId),
    gte(bookingAttempts.createdAt, since)
  );

  if ("customerRef" in input) {
    const { customerRef } = input;
    await db.insert(bookingAttempts).values({ organizationId, customerRef });
    const [byRef] = await db
      .select({ n: count() })
      .from(bookingAttempts)
      .where(and(recent, eq(bookingAttempts.customerRef, customerRef)));
    return (byRef?.n ?? 0) > MAX_PER_CUSTOMER_REF ? THROTTLED_MESSAGE : null;
  }

  const { ip, email } = input;
  await db.insert(bookingAttempts).values({ organizationId, ip, email });

  const [byEmail] = await db
    .select({ n: count() })
    .from(bookingAttempts)
    .where(and(recent, eq(bookingAttempts.email, email)));
  if ((byEmail?.n ?? 0) > MAX_PER_EMAIL) return THROTTLED_MESSAGE;

  // A missing forwarded-for is not a shared identity — it would put every
  // such caller in one bucket — so it is simply not counted.
  if (ip) {
    const [byIp] = await db
      .select({ n: count() })
      .from(bookingAttempts)
      .where(and(recent, eq(bookingAttempts.ip, ip)));
    if ((byIp?.n ?? 0) > MAX_PER_IP) return THROTTLED_MESSAGE;
  }

  return null;
}
```

At the end of the file's existing top doc comment, add this paragraph:

```ts
 *
 * The chat assistant's attempts are counted per customer ref instead of per
 * address and email — see ThrottleIdentity.
```

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/booking/throttle.integration.test.ts`

Expected: PASS (10 tests: the 7 existing and the 3 new).

- [ ] **Step 5: Types, lint, commit**

```bash
bun run typecheck
bun run check:fix && bun run check
git add lib/booking/throttle.ts lib/booking/throttle.integration.test.ts
git commit -m "feat: throttle a chat customer by their customer ref" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The slot lookup can leave one booking out

**Files:**
- Modify: `lib/booking/get-available-slots.ts`
- Create: `lib/booking/available-slots.integration.test.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  ```ts
  export type SlotOptions = { excludeBookingId?: string };
  export function getDaySlots(business: BusinessContext, serviceId: string, dateISO: string, now?: Date, options?: SlotOptions): Promise<DaySlot[] | null>;
  export function getAvailableSlots(business: BusinessContext, serviceId: string, dateISO: string, now?: Date, options?: SlotOptions): Promise<Date[] | null>;
  ```
  Existing callers are unchanged, because `options` defaults to `{}`.

- [ ] **Step 1: Write the failing test**

Create `lib/booking/available-slots.integration.test.ts`:

```ts
/**
 * The slot lookup's one option: leaving a booking out of the busy times, so
 * a booking being moved is not refused by its own current time.
 */
import { eq } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { db } from "@/lib/db";
import { organization } from "@/lib/db/schema/auth-schema";
import { availabilityRules } from "@/lib/db/schema/availability-schema";
import { bookings } from "@/lib/db/schema/booking-schema";
import { businessProfiles } from "@/lib/db/schema/business-schema";
import { services } from "@/lib/db/schema/service-schema";
import {
  type BusinessContext,
  getAvailableSlots,
  getBusinessBySlug,
} from "./get-available-slots";

const ORG = "org_itest_available_slots";
const SLUG = "itest-available-slots";
/** A UTC business, so a local date is simply the UTC date. */
const DAY = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
let business: BusinessContext;
let serviceId: string;
let bookingId: string;

beforeAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db
    .insert(organization)
    .values({ id: ORG, name: ORG, slug: SLUG, createdAt: new Date() });
  await db.insert(businessProfiles).values({
    organizationId: ORG,
    timezone: "UTC",
    minLeadTimeMinutes: 0,
  });
  const [service] = await db
    .insert(services)
    .values({
      organizationId: ORG,
      name: "Cilt Bakımı",
      durationMinutes: 60,
      priceCents: 150_000,
    })
    .returning({ id: services.id });
  serviceId = service.id;
  // Open 09:00–12:00 on DAY only: five 60-minute starts on the 30-minute grid.
  await db.insert(availabilityRules).values({
    organizationId: ORG,
    weekday: new Date(`${DAY}T12:00:00Z`).getUTCDay(),
    startMinutes: 9 * 60,
    endMinutes: 12 * 60,
  });
  const [booked] = await db
    .insert(bookings)
    .values({
      organizationId: ORG,
      serviceId,
      customerName: "Ayşe Yılmaz",
      startsAt: new Date(`${DAY}T10:00:00Z`),
      endsAt: new Date(`${DAY}T11:00:00Z`),
      status: "confirmed",
    })
    .returning({ id: bookings.id });
  bookingId = booked.id;
  const found = await getBusinessBySlug(SLUG);
  if (!found) throw new Error("seeded business not found");
  business = found;
});

afterAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db.$client.end();
});

const times = (slots: Date[] | null) =>
  (slots ?? []).map((slot) => slot.toISOString().slice(11, 16));

describe("getAvailableSlots", () => {
  it("leaves out every start that would overlap a booking", async () => {
    expect(times(await getAvailableSlots(business, serviceId, DAY))).toEqual([
      "09:00",
      "11:00",
    ]);
  });

  it("ignores the booking it is told to, so that booking can move onto its own time", async () => {
    const slots = await getAvailableSlots(business, serviceId, DAY, new Date(), {
      excludeBookingId: bookingId,
    });
    expect(times(slots)).toEqual(["09:00", "09:30", "10:00", "10:30", "11:00"]);
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/booking/available-slots.integration.test.ts`

Expected: the first test passes, and the second FAILS with only `["09:00", "11:00"]`, because the option is ignored.

- [ ] **Step 3: Implement**

In `lib/booking/get-available-slots.ts`:
1. Add `ne` to the `drizzle-orm` import.
2. Add this type below `BusinessContext`:

```ts
export type SlotOptions = {
  /**
   * A booking to leave out of the busy times — the one being moved, so a move
   * that overlaps its own current time is not refused by itself.
   */
  excludeBookingId?: string;
};
```

3. Change the signature of `getDaySlots` to:

```ts
export async function getDaySlots(
  business: BusinessContext,
  serviceId: string,
  dateISO: string,
  now: Date = new Date(),
  options: SlotOptions = {}
): Promise<DaySlot[] | null> {
```

4. In its `existing` query's `.where(and(…))`, add this line after `lt(bookings.startsAt, windowEnd),`. drizzle's `and()` skips `undefined`.

```ts
        options.excludeBookingId
          ? ne(bookings.id, options.excludeBookingId)
          : undefined,
```

5. Replace `getAvailableSlots` with:

```ts
export async function getAvailableSlots(
  business: BusinessContext,
  serviceId: string,
  dateISO: string,
  now: Date = new Date(),
  options: SlotOptions = {}
): Promise<Date[] | null> {
  const slots = await getDaySlots(business, serviceId, dateISO, now, options);
  if (slots === null) return null;
  return slots.filter((slot) => !slot.taken).map((slot) => slot.start);
}
```

Keep the existing doc comment above `getAvailableSlots`.

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/booking/available-slots.integration.test.ts`

Expected: PASS (2 tests).

- [ ] **Step 5: Types, lint, commit**

```bash
bun run typecheck
bun run check:fix && bun run check
git add lib/booking/get-available-slots.ts lib/booking/available-slots.integration.test.ts
git commit -m "feat: let the slot lookup leave out the booking being moved" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The create-booking core, shared by the booking page and the assistant

**Files:**
- Create: `lib/booking/create-booking-core.ts`
- Modify: `lib/actions/bookings.ts` (`createBooking` becomes a wrapper; imports trimmed)
- Create: `lib/booking/create-booking-core.integration.test.ts`

**Interfaces:**
- Consumes: `ThrottleIdentity` and `checkBookingThrottle` (Task 2); `getAvailableSlots` (Task 3); `bookings.source` and `bookings.customerRef` (Task 1).
- Produces:
  ```ts
  export type CreateBookingCoreInput = {
    business: BusinessContext; serviceId: string; startsAt: Date;
    customerName: string; customerEmail: string | null; customerPhone: string | null;
    source: "web" | "assistant"; customerRef: string | null;
    throttle: ThrottleIdentity; customerIp: string;
    expectDepositCents?: number | null;
  };
  export type CreateBookingErrorCode = "throttled" | "not_found" | "slot_unavailable"
    | "terms_changed" | "email_required" | "slot_taken" | "payment_start_failed";
  export type CreateBookingCoreResult =
    | { ok: true; booking: Booking; checkoutUrl?: string; holdExpiresAt?: Date }
    | { ok: false; code: CreateBookingErrorCode; message: string };
  export function createBookingCore(input: CreateBookingCoreInput): Promise<CreateBookingCoreResult>;
  ```
  Here `Booking` is `typeof bookings.$inferSelect`.

- [ ] **Step 1: Write the failing test**

Create `lib/booking/create-booking-core.integration.test.ts`:

```ts
/**
 * The booking rules the booking page and the chat assistant share, called the
 * way the assistant calls them: no email unless there is a kapora, a customer
 * ref instead of an address, and the kapora the customer was shown. The
 * page's own behaviour stays pinned by lib/actions/bookings*.integration.test.ts,
 * which now run through this core.
 */
import { and, eq } from "drizzle-orm";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

const { confirmedMock, createDepositCheckout } = vi.hoisted(() => ({
  confirmedMock: vi.fn(),
  createDepositCheckout: vi.fn(async (_input: Record<string, unknown>) => ({
    token: `tok_${Math.random().toString(36).slice(2)}`,
    paymentPageUrl: "https://sandbox-cpp.iyzipay.com/checkout",
  })),
}));

vi.mock("@/lib/email/booking-notifications", () => ({
  sendBookingConfirmedEmails: confirmedMock,
  sendBookingCancelledEmails: vi.fn(),
}));
vi.mock("@/lib/payments", () => ({
  getPaymentProvider: () => ({ createDepositCheckout }),
}));

const { createBookingCore } = await import("./create-booking-core");
const { getBusinessBySlug } = await import("./get-available-slots");
const { db } = await import("@/lib/db");
const { organization } = await import("@/lib/db/schema/auth-schema");
const { availabilityRules } = await import(
  "@/lib/db/schema/availability-schema"
);
const { orgPayoutAccounts, orgSubscriptions } = await import(
  "@/lib/db/schema/billing-schema"
);
const { bookingAttempts, bookings } = await import(
  "@/lib/db/schema/booking-schema"
);
const { businessProfiles } = await import("@/lib/db/schema/business-schema");
const { services } = await import("@/lib/db/schema/service-schema");

type CoreInput = Parameters<typeof createBookingCore>[0];

const ORG = "org_itest_create_core";
const SLUG = "itest-create-core";
const ALI = `telegram:${"a".repeat(26)}`;
const VELI = `telegram:${"b".repeat(26)}`;
const DEPOSIT = 30_000;
const SUB_MERCHANT_KEY = "sub_merchant_itest_create_core";
const tomorrow = new Date(Date.now() + 86_400_000);

let business: CoreInput["business"];
let haircut: string; // no kapora
let facial: string; // kapora
let retired: string; // switched off

function at(hourUTC: number, minute = 0): Date {
  const d = new Date(tomorrow);
  d.setUTCHours(hourUTC, minute, 0, 0);
  return d;
}

/** An assistant booking for Ali, unless overridden. */
function asAssistant(overrides: Partial<CoreInput> = {}): CoreInput {
  return {
    business,
    serviceId: haircut,
    startsAt: at(9),
    customerName: "Ali Yılmaz",
    customerEmail: null,
    customerPhone: "0532 111 22 33",
    source: "assistant",
    customerRef: ALI,
    throttle: { customerRef: ALI },
    customerIp: "85.34.78.112",
    ...overrides,
  };
}

/** Veli instead of Ali — another chat customer with their own throttle. */
const asVeli = (overrides: Partial<CoreInput> = {}) =>
  asAssistant({ customerRef: VELI, throttle: { customerRef: VELI }, ...overrides });

const rowsFor = (customerRef: string) =>
  db.query.bookings.findMany({
    where: and(
      eq(bookings.organizationId, ORG),
      eq(bookings.customerRef, customerRef)
    ),
  });

beforeAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db
    .insert(organization)
    .values({ id: ORG, name: "ITest Pro", slug: SLUG, createdAt: new Date() });
  await db.insert(businessProfiles).values({
    organizationId: ORG,
    timezone: "UTC",
    minLeadTimeMinutes: 0,
  });
  const rows = await db
    .insert(services)
    .values([
      {
        organizationId: ORG,
        name: "Saç Kesimi",
        durationMinutes: 30,
        priceCents: 80_000,
      },
      {
        organizationId: ORG,
        name: "Cilt Bakımı",
        durationMinutes: 30,
        priceCents: 150_000,
        depositCents: DEPOSIT,
      },
      {
        organizationId: ORG,
        name: "Eski Hizmet",
        durationMinutes: 30,
        priceCents: 10_000,
        active: false,
      },
    ])
    .returning({ id: services.id });
  [haircut, facial, retired] = rows.map((row) => row.id);
  await db.insert(availabilityRules).values({
    organizationId: ORG,
    weekday: tomorrow.getUTCDay(),
    startMinutes: 9 * 60,
    endMinutes: 17 * 60,
  });
  // Pro with an approved submerchant: kapora is collected.
  await db.insert(orgSubscriptions).values({
    organizationId: ORG,
    plan: "pro",
    status: "active",
    currentPeriodEndsAt: new Date(Date.now() + 30 * 86_400_000),
    lastSyncedAt: new Date(),
  });
  await db.insert(orgPayoutAccounts).values({
    organizationId: ORG,
    status: "active",
    subMerchantKey: SUB_MERCHANT_KEY,
    subMerchantExternalId: ORG,
    merchantType: "personal",
    name: "ITest Pro",
    contactName: "Ada",
    contactSurname: "Lovelace",
    identityNumber: "10000000146",
    iban: "TR180006200119000006672315",
    address: "Kadıköy/İstanbul",
    gsmNumber: "+905350000000",
    email: "pro@test.dev",
  });
  const found = await getBusinessBySlug(SLUG);
  if (!found) throw new Error("seeded business not found");
  business = found;
});

beforeEach(async () => {
  await db.delete(bookings).where(eq(bookings.organizationId, ORG));
  await db
    .delete(bookingAttempts)
    .where(eq(bookingAttempts.organizationId, ORG));
  confirmedMock.mockClear();
  createDepositCheckout.mockClear();
});

afterAll(async () => {
  // Cascades to profile, services, rules, bookings, subscription, payout.
  await db.delete(organization).where(eq(organization.id, ORG));
  await db.$client.end();
});

describe("createBookingCore — for the chat assistant", () => {
  it("books a chat customer with no email, records whose it is, and still tells the owner", async () => {
    const result = await createBookingCore(asAssistant());

    expect(result.ok).toBe(true);
    const [row] = await rowsFor(ALI);
    expect(row).toMatchObject({
      status: "confirmed",
      source: "assistant",
      customerRef: ALI,
      customerEmail: null,
      depositCents: null,
    });
    // No customer address to write to — but the owner hears about it.
    expect(confirmedMock).toHaveBeenCalledWith(
      expect.objectContaining({ id: row.id })
    );
  });

  it("asks for an email before a kapora, and holds nothing", async () => {
    const result = await createBookingCore(asAssistant({ serviceId: facial }));

    expect(result).toMatchObject({ ok: false, code: "email_required" });
    expect(await rowsFor(ALI)).toHaveLength(0);
    expect(createDepositCheckout).not.toHaveBeenCalled();
  });

  it("holds the slot for 30 minutes and returns the payment page", async () => {
    const result = await createBookingCore(
      asAssistant({
        serviceId: facial,
        customerEmail: "ali@test.dev",
        expectDepositCents: DEPOSIT,
      })
    );

    if (!result.ok) throw new Error(result.message);
    expect(result.checkoutUrl).toBe("https://sandbox-cpp.iyzipay.com/checkout");
    expect(result.booking.status).toBe("pending");
    const heldFor = ((result.holdExpiresAt?.getTime() ?? 0) - Date.now()) / 60_000;
    expect(heldFor).toBeGreaterThan(29);
    expect(heldFor).toBeLessThanOrEqual(30);
    expect(createDepositCheckout.mock.calls[0][0]).toMatchObject({
      customerEmail: "ali@test.dev",
      depositCents: DEPOSIT,
      subMerchantKey: SUB_MERCHANT_KEY,
    });
  });

  it("refuses when the kapora is not the one the customer agreed to", async () => {
    const results = [
      // Shown less than the service takes.
      await createBookingCore(
        asAssistant({
          serviceId: facial,
          customerEmail: "ali@test.dev",
          expectDepositCents: 20_000,
        })
      ),
      // Told there was none.
      await createBookingCore(
        asAssistant({
          serviceId: facial,
          customerEmail: "ali@test.dev",
          expectDepositCents: null,
          startsAt: at(10),
        })
      ),
      // Shown one on a service that takes none.
      await createBookingCore(
        asAssistant({ expectDepositCents: DEPOSIT, startsAt: at(11) })
      ),
    ];

    for (const result of results) {
      expect(result).toMatchObject({ ok: false, code: "terms_changed" });
    }
    expect(await rowsFor(ALI)).toHaveLength(0);
    expect(createDepositCheckout).not.toHaveBeenCalled();
  });

  it("books when the customer was told, rightly, that there is no kapora", async () => {
    const result = await createBookingCore(
      asAssistant({ expectDepositCents: null })
    );
    expect(result.ok).toBe(true);
  });

  it("will not book a service the business has switched off", async () => {
    expect(
      await createBookingCore(asAssistant({ serviceId: retired }))
    ).toMatchObject({ ok: false, code: "not_found" });
  });

  it("stops a chat customer after three attempts in an hour", async () => {
    // Off the slot grid: each attempt is refused, and each still counts.
    for (let i = 0; i < 3; i += 1) {
      expect(
        await createBookingCore(asAssistant({ startsAt: at(9, 7) }))
      ).toMatchObject({ code: "slot_unavailable" });
    }
    expect(await createBookingCore(asAssistant())).toMatchObject({
      code: "throttled",
    });
    // Another chat customer is not held back by Ali's attempts.
    expect((await createBookingCore(asVeli())).ok).toBe(true);
  });

  it("tells the loser of a race the slot was just taken", async () => {
    const outcomes = await Promise.all([
      createBookingCore(asAssistant({ startsAt: at(14) })),
      createBookingCore(asVeli({ startsAt: at(14) })),
    ]);
    const codes = outcomes.map((r) => (r.ok ? "ok" : r.code)).sort();

    expect(codes[0]).toBe("ok");
    expect(["slot_taken", "slot_unavailable"]).toContain(codes[1]);
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/booking/create-booking-core.integration.test.ts`

Expected: FAIL. `./create-booking-core` cannot be resolved.

- [ ] **Step 3: Implement the core**

Create `lib/booking/create-booking-core.ts`:

```ts
import "server-only";
import { and, eq, sql } from "drizzle-orm";
import { getBilling } from "@/lib/billing/get-billing";
import { expireHoldsForOrg } from "@/lib/booking/expire-holds";
import {
  type BusinessContext,
  getAvailableSlots,
  localDateISO,
} from "@/lib/booking/get-available-slots";
import {
  checkBookingThrottle,
  type ThrottleIdentity,
} from "@/lib/booking/throttle";
import { db } from "@/lib/db";
import { isExclusionConflict } from "@/lib/db/errors";
import { bookings } from "@/lib/db/schema/booking-schema";
import { services } from "@/lib/db/schema/service-schema";
import { sendBookingConfirmedEmails } from "@/lib/email/booking-notifications";
import { requireEnv } from "@/lib/env";
import { getPaymentProvider } from "@/lib/payments";

type Booking = typeof bookings.$inferSelect;

/** How long a held slot waits for its kapora before it is released. */
const HOLD_MINUTES = 30;

export type CreateBookingCoreInput = {
  business: BusinessContext;
  serviceId: string;
  startsAt: Date;
  customerName: string;
  customerEmail: string | null;
  customerPhone: string | null;
  /** The public booking form, or the chat assistant on a customer's behalf. */
  source: "web" | "assistant";
  /** Whose booking it is, for the assistant; null from the form. */
  customerRef: string | null;
  throttle: ThrottleIdentity;
  /** For iyzico's basket — FALLBACK_IP when the caller has no address. */
  customerIp: string;
  /**
   * The kapora the customer was shown and agreed to, null meaning "none".
   * Left undefined, nothing is compared: the booking page shows the kapora
   * on the same render it books from, so there is nothing to drift.
   */
  expectDepositCents?: number | null;
};

export type CreateBookingErrorCode =
  | "throttled"
  | "not_found"
  | "slot_unavailable"
  | "terms_changed"
  | "email_required"
  | "slot_taken"
  | "payment_start_failed";

export type CreateBookingCoreResult =
  | { ok: true; booking: Booking; checkoutUrl?: string; holdExpiresAt?: Date }
  | { ok: false; code: CreateBookingErrorCode; message: string };

function fail(
  code: CreateBookingErrorCode,
  message: string
): CreateBookingCoreResult {
  return { ok: false, code, message };
}

/**
 * Book a slot — the rules every way in shares: the booking page's server
 * action and the chat assistant's create_booking tool.
 *
 * Every check the page made before is still made here, in the same order,
 * and the database still has the last word: the exclusion constraint decides
 * a race, whoever is racing. What stays with each caller is what only it
 * knows — the page's redirect, the assistant's customer ref.
 */
export async function createBookingCore(
  input: CreateBookingCoreInput
): Promise<CreateBookingCoreResult> {
  const { business, serviceId, startsAt } = input;

  // Before the slot work, so a flood costs the database as little as
  // possible. A failed attempt still counts against the caller.
  const throttled = await checkBookingThrottle({
    organizationId: business.organizationId,
    ...input.throttle,
  });
  if (throttled) return fail("throttled", throttled);

  const service = await db.query.services.findFirst({
    where: and(
      eq(services.id, serviceId),
      eq(services.organizationId, business.organizationId),
      eq(services.active, true)
    ),
  });
  if (!service) return fail("not_found", "Hizmet bulunamadı");

  // Never trust the caller: the requested instant must be in the freshly
  // computed availability for its local business day.
  const dateISO = localDateISO(startsAt, business.profile.timezone);
  const slots = await getAvailableSlots(business, serviceId, dateISO);
  if (!slots?.some((slot) => slot.getTime() === startsAt.getTime())) {
    return fail(
      "slot_unavailable",
      "Bu saat artık müsait değil — lütfen başka bir saat seçin."
    );
  }

  await expireHoldsForOrg(business.organizationId);

  // Entitlements come straight from the business's own rows — a DB read only,
  // never an iyzico call on a booking. A stored kapora on a lapsed plan is
  // kept but not collected, so the booking follows the plan rather than the
  // service row, or it would claim a deposit nobody was ever charged.
  const billing = await getBilling(business.organizationId);
  const depositCents =
    service.depositCents != null && billing.onlineDeposit
      ? service.depositCents
      : null;

  // The customer agreed to the kapora they were shown. If the owner changed
  // it since, they agreed to something else — ask again rather than charge.
  if (
    input.expectDepositCents !== undefined &&
    input.expectDepositCents !== depositCents
  ) {
    return fail(
      "terms_changed",
      "Hizmetin kaporası değişmiş — lütfen yeniden onaylayın."
    );
  }

  // iyzico takes no payment without the buyer's email. The form always asks
  // for one; a chat customer is asked only when there is a kapora.
  let deposit: { cents: number; email: string } | null = null;
  if (depositCents != null) {
    if (!input.customerEmail) {
      return fail(
        "email_required",
        "Kapora ödemesi için e-posta adresi gerekli."
      );
    }
    deposit = { cents: depositCents, email: input.customerEmail };
  }

  const endsAt = new Date(
    startsAt.getTime() + service.durationMinutes * 60_000
  );

  let created: Booking;
  try {
    [created] = await db
      .insert(bookings)
      .values({
        organizationId: business.organizationId,
        serviceId,
        customerName: input.customerName,
        customerEmail: input.customerEmail,
        customerPhone: input.customerPhone,
        startsAt,
        endsAt,
        status: deposit ? "pending" : "confirmed",
        depositCents: deposit?.cents ?? null,
        expiresAt: deposit
          ? new Date(Date.now() + HOLD_MINUTES * 60_000)
          : null,
        source: input.source,
        customerRef: input.customerRef,
      })
      .returning();
  } catch (err) {
    // bookings_no_overlap — including the deadlock a same-instant race can
    // end in (see lib/db/errors.ts).
    if (isExclusionConflict(err)) {
      return fail(
        "slot_taken",
        "Bu saat az önce doldu — lütfen başka bir saat seçin."
      );
    }
    throw err;
  }

  if (!deposit) {
    await sendBookingConfirmedEmails(created);
    return { ok: true, booking: created };
  }

  try {
    const subMerchantKey = billing.payoutAccount?.subMerchantKey;
    if (!subMerchantKey) {
      // Unreachable in practice — onlineDeposit requires an active payout
      // account, and payout_accounts_active_has_key requires an active
      // account to carry a key. Throwing reuses the hold release below
      // rather than inventing a second failure path.
      throw new Error("Entitled for deposits but no submerchant key");
    }
    const checkout = await getPaymentProvider().createDepositCheckout({
      bookingId: created.id,
      serviceName: service.name,
      businessName: business.orgName,
      depositCents: deposit.cents,
      currency: business.profile.currency,
      customerName: input.customerName,
      customerEmail: deposit.email,
      customerIp: input.customerIp,
      subMerchantKey,
      appUrl: requireEnv("NEXT_PUBLIC_APP_URL"),
    });
    if (!checkout.paymentPageUrl) {
      throw new Error("Payment provider returned no hosted page URL");
    }
    await db
      .update(bookings)
      .set({ paymentToken: checkout.token })
      .where(eq(bookings.id, created.id));
    return {
      ok: true,
      booking: { ...created, paymentToken: checkout.token },
      checkoutUrl: checkout.paymentPageUrl,
      holdExpiresAt: created.expiresAt ?? undefined,
    };
  } catch (err) {
    // Couldn't start payment: release the hold instead of stranding it.
    await db
      .update(bookings)
      .set({ status: "cancelled", cancelledAt: sql`now()` })
      .where(eq(bookings.id, created.id));
    console.error("Failed to create deposit checkout:", err);
    return fail(
      "payment_start_failed",
      "Ödeme başlatılamadı — lütfen tekrar deneyin."
    );
  }
}
```

- [ ] **Step 4: Run the core's tests**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/booking/create-booking-core.integration.test.ts`

Expected: PASS (8 tests).

- [ ] **Step 5: Turn `createBooking` into a wrapper**

In `lib/actions/bookings.ts`:

1. Delete the `isOverlapError` helper and its doc comment.
2. Replace the whole `createBooking` function, keeping `createBookingSchema`, `CreateBookingInput` and `ActionResult` above it, with:

```ts
export async function createBooking(
  input: CreateBookingInput
): Promise<ActionResult> {
  const parsed = createBookingSchema.safeParse(input);
  if (!parsed.success) {
    return { error: parsed.error.issues[0]?.message ?? "Geçersiz bilgi" };
  }
  const { slug, serviceId, customerName, customerEmail, customerPhone } =
    parsed.data;

  const business = await getBusinessBySlug(slug);
  if (!business) return { error: "İşletme bulunamadı" };

  const ip = await clientIp();
  const result = await createBookingCore({
    business,
    serviceId,
    startsAt: new Date(parsed.data.startsAt),
    customerName,
    customerEmail,
    customerPhone,
    source: "web",
    customerRef: null,
    throttle: { ip, email: customerEmail },
    customerIp: ip ?? FALLBACK_IP,
  });
  if (!result.ok) return { error: result.message };

  // Outside any try: redirect() works by throwing.
  return redirect(
    result.checkoutUrl ?? confirmationPath(slug, result.booking.id)
  );
}
```

3. Replace the import block at the top of the file with:

```ts
import { and, eq, gt, sql } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { z } from "zod";
import { requireOwner } from "@/lib/auth-guard";
import {
  customerCanCancel,
  refundsDeposit,
} from "@/lib/booking/cancellation-policy";
import { clientIp, FALLBACK_IP } from "@/lib/booking/client-ip";
import {
  confirmationPath,
  getBookingForPage,
} from "@/lib/booking/confirmation";
import { createBookingCore } from "@/lib/booking/create-booking-core";
import { getBusinessBySlug } from "@/lib/booking/get-available-slots";
import { refundDeposit } from "@/lib/booking/refund-deposit";
import { db } from "@/lib/db";
import { bookings } from "@/lib/db/schema/booking-schema";
import { sendBookingCancelledEmails } from "@/lib/email/booking-notifications";
import { optionalPhoneSchema } from "@/lib/phone";
```

`cancelBooking` and `cancelBookingAsCustomer` stay as they are in this task.

- [ ] **Step 6: The page's behaviour is unchanged**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/actions/bookings.integration.test.ts lib/actions/bookings-billing.integration.test.ts lib/actions/booking-race.integration.test.ts lib/booking/create-booking-core.integration.test.ts
```

Expected: PASS for all four files, with no edits to the three existing test files.

- [ ] **Step 7: Whole suite, types, lint, commit**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test
bun run typecheck
bun run check:fix && bun run check
git add lib/booking/create-booking-core.ts lib/booking/create-booking-core.integration.test.ts lib/actions/bookings.ts
git commit -m "refactor: move the booking rules into a core the assistant can share" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The customer-cancel core, authorised by booking id or by customer ref

**Files:**
- Create: `lib/booking/customer-cancel-core.ts`
- Modify: `lib/actions/bookings.ts` (`cancelBookingAsCustomer` becomes a wrapper; imports trimmed)
- Create: `lib/booking/customer-cancel-core.integration.test.ts`

**Interfaces:**
- Consumes: `bookings.customerRef` (Task 1).
- Produces:
  ```ts
  export type CustomerCancelCoreInput = {
    organizationId: string; bookingId: string; customerRef: string | null;
    expectRefund: boolean; customerIp: string; now?: Date;
  };
  export type CustomerCancelErrorCode = "not_found" | "already_cancelled" | "started" | "refund_window_closed";
  export type CustomerCancelCoreResult =
    | { ok: true; booking: Booking; depositRefunded: boolean; depositKept: boolean; refundFailed: boolean }
    | { ok: false; code: CustomerCancelErrorCode; message: string };
  export function customerCancelCore(input: CustomerCancelCoreInput): Promise<CustomerCancelCoreResult>;
  ```

- [ ] **Step 1: Write the failing test**

Create `lib/booking/customer-cancel-core.integration.test.ts`:

```ts
/**
 * A chat customer cancels through the assistant. The 24-hour kapora rule
 * and the page's behaviour are pinned by lib/actions/customer-cancel
 * .integration.test.ts, which now runs through this core; what is tested here
 * is the assistant's credential — the customer ref, not the booking id.
 */
import { eq } from "drizzle-orm";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

const { refundMock, cancelledEmail } = vi.hoisted(() => ({
  refundMock: vi.fn(),
  cancelledEmail: vi.fn(),
}));

vi.mock("@/lib/payments", () => ({
  getPaymentProvider: () => ({ refundTransaction: refundMock }),
}));
vi.mock("@/lib/email/booking-notifications", () => ({
  sendBookingConfirmedEmails: vi.fn(),
  sendBookingCancelledEmails: (...args: unknown[]) => cancelledEmail(...args),
}));

const { customerCancelCore } = await import("./customer-cancel-core");
const { db } = await import("@/lib/db");
const { organization } = await import("@/lib/db/schema/auth-schema");
const { bookings } = await import("@/lib/db/schema/booking-schema");
const { businessProfiles } = await import("@/lib/db/schema/business-schema");
const { services } = await import("@/lib/db/schema/service-schema");

const ORG = "org_itest_cancel_core";
const ALI = `telegram:${"a".repeat(26)}`;
const VELI = `telegram:${"b".repeat(26)}`;
const TX_ID = "41234567";
const HOUR = 3_600_000;
let serviceId: string;

beforeAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db.insert(organization).values({
    id: ORG,
    name: ORG,
    slug: "itest-cancel-core",
    createdAt: new Date(),
  });
  await db
    .insert(businessProfiles)
    .values({ organizationId: ORG, timezone: "Europe/Istanbul" });
  const [service] = await db
    .insert(services)
    .values({
      organizationId: ORG,
      name: "Cilt Bakımı",
      durationMinutes: 60,
      priceCents: 150_000,
      depositCents: 30_000,
    })
    .returning({ id: services.id });
  serviceId = service.id;
});

let slot = 0;

/** Ali's paid, confirmed booking `hoursAway` from now, unless overridden. */
async function seedBooking(
  hoursAway: number,
  overrides: Partial<typeof bookings.$inferInsert> = {}
) {
  // A distinct minute each, so bookings_no_overlap never trips.
  slot += 1;
  const startsAt = new Date(Date.now() + hoursAway * HOUR + slot * 61 * 60_000);
  const [row] = await db
    .insert(bookings)
    .values({
      organizationId: ORG,
      serviceId,
      customerName: "Ali Yılmaz",
      startsAt,
      endsAt: new Date(startsAt.getTime() + 60 * 60_000),
      status: "confirmed",
      depositCents: 30_000,
      paymentToken: `tok_${Math.random()}`,
      paymentTransactionId: TX_ID,
      source: "assistant",
      customerRef: ALI,
      ...overrides,
    })
    .returning();
  return row;
}

const reload = (id: string) =>
  db.query.bookings.findFirst({ where: eq(bookings.id, id) });

const cancel = (
  bookingId: string,
  customerRef: string | null = ALI,
  expectRefund = false
) =>
  customerCancelCore({
    organizationId: ORG,
    bookingId,
    customerRef,
    expectRefund,
    customerIp: "85.34.78.112",
  });

beforeEach(async () => {
  slot = 0;
  await db.delete(bookings).where(eq(bookings.organizationId, ORG));
  refundMock
    .mockReset()
    .mockResolvedValue({ refunded: true, errorMessage: null });
  cancelledEmail.mockReset();
});

afterAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db.$client.end();
});

describe("customerCancelCore — through the assistant", () => {
  it("cancels a chat customer's own booking, refunding with a day's notice", async () => {
    const booking = await seedBooking(7 * 24);

    const result = await cancel(booking.id, ALI, true);

    expect(result).toMatchObject({
      ok: true,
      depositRefunded: true,
      depositKept: false,
      refundFailed: false,
    });
    const after = await reload(booking.id);
    expect(after?.status).toBe("cancelled");
    expect(after?.cancelledBy).toBe("customer");
  });

  it("refuses another chat customer's booking, even with its real id", async () => {
    const theirs = await seedBooking(7 * 24, { customerRef: VELI });

    expect(await cancel(theirs.id, ALI)).toMatchObject({
      ok: false,
      code: "not_found",
    });
    expect((await reload(theirs.id))?.status).toBe("confirmed");
    expect(refundMock).not.toHaveBeenCalled();
  });

  it("refuses a booking made on the page when a chat customer asks", async () => {
    const onThePage = await seedBooking(7 * 24, {
      source: "web",
      customerRef: null,
      customerEmail: "ayse@test.dev",
    });

    expect(await cancel(onThePage.id, ALI)).toMatchObject({
      ok: false,
      code: "not_found",
    });
    expect((await reload(onThePage.id))?.status).toBe("confirmed");
  });

  it("cancels nothing when the refund it promised is no longer due", async () => {
    const booking = await seedBooking(3);

    expect(await cancel(booking.id, ALI, true)).toMatchObject({
      ok: false,
      code: "refund_window_closed",
    });
    expect((await reload(booking.id))?.status).toBe("confirmed");
    expect(refundMock).not.toHaveBeenCalled();
  });

  it("keeps the kapora inside the last day when that is what the customer was told", async () => {
    const booking = await seedBooking(3);

    expect(await cancel(booking.id, ALI, false)).toMatchObject({
      ok: true,
      depositRefunded: false,
      depositKept: true,
    });
    expect(refundMock).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/booking/customer-cancel-core.integration.test.ts`

Expected: FAIL. `./customer-cancel-core` cannot be resolved.

- [ ] **Step 3: Implement the core**

Create `lib/booking/customer-cancel-core.ts`:

```ts
import "server-only";
import { and, eq, gt, sql } from "drizzle-orm";
import {
  customerCanCancel,
  refundsDeposit,
} from "@/lib/booking/cancellation-policy";
import { getBookingForPage } from "@/lib/booking/confirmation";
import { refundDeposit } from "@/lib/booking/refund-deposit";
import { db } from "@/lib/db";
import { bookings } from "@/lib/db/schema/booking-schema";
import { sendBookingCancelledEmails } from "@/lib/email/booking-notifications";

type Booking = typeof bookings.$inferSelect;

export type CustomerCancelCoreInput = {
  organizationId: string;
  bookingId: string;
  /**
   * The assistant's customer. When set, the booking must be theirs — the id
   * alone is not enough, since the model saw it in a tool result. Null on the
   * booking page, where the uuid in the link is the whole credential.
   */
  customerRef: string | null;
  /** What the customer was told: that their kapora would come back. */
  expectRefund: boolean;
  customerIp: string;
  now?: Date;
};

export type CustomerCancelErrorCode =
  | "not_found"
  | "already_cancelled"
  | "started"
  | "refund_window_closed";

export type CustomerCancelCoreResult =
  | {
      ok: true;
      booking: Booking;
      depositRefunded: boolean;
      /** Cancelled inside the last day: the business keeps the kapora. */
      depositKept: boolean;
      /** The kapora was due back, and iyzico refused or failed. */
      refundFailed: boolean;
    }
  | { ok: false; code: CustomerCancelErrorCode; message: string };

function fail(
  code: CustomerCancelErrorCode,
  message: string
): CustomerCancelCoreResult {
  return { ok: false, code, message };
}

/**
 * The customer calls their own appointment off — from its page, or through
 * the chat assistant.
 *
 * The kapora comes back with a day's notice and stays with the business
 * after that (lib/booking/cancellation-policy.ts); either way the slot is
 * freed at once. `expectRefund` is what the customer was told: if the
 * deadline passed since, nothing is cancelled and they are asked again
 * rather than surprised.
 */
export async function customerCancelCore(
  input: CustomerCancelCoreInput
): Promise<CustomerCancelCoreResult> {
  const now = input.now ?? new Date();

  const found = await getBookingForPage(input.organizationId, input.bookingId);
  if (
    !found ||
    (input.customerRef !== null &&
      found.booking.customerRef !== input.customerRef)
  ) {
    return fail("not_found", "Randevu bulunamadı.");
  }
  const { booking } = found;

  if (booking.status === "cancelled") {
    return fail("already_cancelled", "Bu randevu zaten iptal edilmiş.");
  }
  if (!customerCanCancel(booking, now)) {
    return fail("started", "Başlamış bir randevu iptal edilemez.");
  }

  const paid =
    booking.paymentTransactionId !== null && booking.depositCents !== null;
  const refundDue = paid && refundsDeposit(booking.startsAt, now);
  if (input.expectRefund && paid && !refundDue) {
    return fail(
      "refund_window_closed",
      "İade süresi doldu: kapora artık iade edilmiyor. Yine de iptal etmek isterseniz tekrar onaylayın."
    );
  }

  // Guarded like the owner's cancel, and against the appointment starting
  // between the check above and this write.
  const [cancelled] = await db
    .update(bookings)
    .set({
      status: "cancelled",
      cancelledAt: sql`now()`,
      cancelledBy: "customer",
    })
    .where(
      and(
        eq(bookings.id, booking.id),
        eq(bookings.organizationId, input.organizationId),
        sql`${bookings.status} <> 'cancelled'`,
        gt(bookings.startsAt, sql`now()`)
      )
    )
    .returning();
  if (!cancelled) {
    return fail("already_cancelled", "Bu randevu zaten iptal edilmiş.");
  }

  let depositRefunded = false;
  if (refundDue) {
    try {
      depositRefunded =
        (await refundDeposit(cancelled.id, input.customerIp)) === "refunded";
    } catch (err) {
      // Cancelled either way; the emails say the refund did not go through.
      console.error(`Kapora refund threw for booking ${cancelled.id}:`, err);
    }
  }
  const outcome = {
    depositKept: paid && !refundDue,
    refundFailed: refundDue && !depositRefunded,
  };

  await sendBookingCancelledEmails(cancelled, depositRefunded, {
    by: "customer",
    ...outcome,
  });
  return { ok: true, booking: cancelled, depositRefunded, ...outcome };
}
```

- [ ] **Step 4: Run the core's tests**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/booking/customer-cancel-core.integration.test.ts`

Expected: PASS (5 tests).

- [ ] **Step 5: Turn `cancelBookingAsCustomer` into a wrapper**

In `lib/actions/bookings.ts`, replace the body of `cancelBookingAsCustomer`. Keep its doc comment, `customerCancelSchema` and `CustomerCancelResult` as they are.

```ts
export async function cancelBookingAsCustomer(
  input: z.input<typeof customerCancelSchema>
): Promise<CustomerCancelResult> {
  const parsed = customerCancelSchema.safeParse(input);
  if (!parsed.success) return { error: "Geçersiz bilgi" };
  const { slug, bookingId, expectRefund } = parsed.data;

  const business = await getBusinessBySlug(slug);
  if (!business) return { error: "Randevu bulunamadı." };

  const result = await customerCancelCore({
    organizationId: business.organizationId,
    bookingId,
    // On the page, the booking's uuid under its own business's address is
    // the whole credential — there is no customer ref to match.
    customerRef: null,
    expectRefund,
    customerIp: (await clientIp()) ?? FALLBACK_IP,
  });
  if (!result.ok) {
    return result.code === "refund_window_closed"
      ? { error: result.message, refundWindowClosed: true }
      : { error: result.message };
  }

  revalidatePath("/panel/randevular");
  revalidatePath("/panel");
  const { depositRefunded, depositKept, refundFailed } = result;
  return { cancelled: true, depositRefunded, depositKept, refundFailed };
}
```

Then replace the file's import block with:

```ts
import { and, eq, sql } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { z } from "zod";
import { requireOwner } from "@/lib/auth-guard";
import { clientIp, FALLBACK_IP } from "@/lib/booking/client-ip";
import { confirmationPath } from "@/lib/booking/confirmation";
import { createBookingCore } from "@/lib/booking/create-booking-core";
import { customerCancelCore } from "@/lib/booking/customer-cancel-core";
import { getBusinessBySlug } from "@/lib/booking/get-available-slots";
import { refundDeposit } from "@/lib/booking/refund-deposit";
import { db } from "@/lib/db";
import { bookings } from "@/lib/db/schema/booking-schema";
import { sendBookingCancelledEmails } from "@/lib/email/booking-notifications";
import { optionalPhoneSchema } from "@/lib/phone";
```

- [ ] **Step 6: The page's behaviour is unchanged**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/actions/customer-cancel.integration.test.ts lib/actions/cancel-booking.integration.test.ts lib/booking/customer-cancel-core.integration.test.ts`

Expected: PASS, with no edits to the existing test files.

- [ ] **Step 7: Whole suite, types, lint, commit**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test
bun run typecheck
bun run check:fix && bun run check
git add lib/booking/customer-cancel-core.ts lib/booking/customer-cancel-core.integration.test.ts lib/actions/bookings.ts
git commit -m "refactor: share the customer-cancel rules with the assistant" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: A customer can move their own booking

**Files:**
- Create: `lib/booking/customer-reschedule-core.ts`
- Create: `lib/booking/customer-reschedule-core.integration.test.ts`

**Interfaces:**
- Consumes: `getAvailableSlots(…, now, { excludeBookingId })` (Task 3); `bookings.customerRef` (Task 1).
- Produces:
  ```ts
  export type CustomerRescheduleCoreInput = {
    business: BusinessContext; bookingId: string; customerRef: string; newStartsAt: Date; now?: Date;
  };
  export type CustomerRescheduleErrorCode = "not_found" | "already_cancelled" | "pending" | "started" | "slot_unavailable" | "slot_taken";
  export type CustomerRescheduleCoreResult =
    | { ok: true; booking: Booking; previousStartsAt: Date }
    | { ok: false; code: CustomerRescheduleErrorCode; message: string };
  export function customerRescheduleCore(input: CustomerRescheduleCoreInput): Promise<CustomerRescheduleCoreResult>;
  ```

- [ ] **Step 1: Write the failing test**

Create `lib/booking/customer-reschedule-core.integration.test.ts`:

```ts
/**
 * A chat customer moves their own booking. Unlike the owner, the customer is
 * held to the slot grid: the new time must be one the booking page would
 * offer — except that the booking's own current time never blocks it.
 */
import { and, eq } from "drizzle-orm";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

const { rescheduledMock } = vi.hoisted(() => ({ rescheduledMock: vi.fn() }));

vi.mock("@/lib/email/booking-notifications", () => ({
  sendBookingConfirmedEmails: vi.fn(),
  sendBookingCancelledEmails: vi.fn(),
  sendBookingRescheduledEmail: rescheduledMock,
}));

const { customerRescheduleCore } = await import("./customer-reschedule-core");
const { getBusinessBySlug } = await import("./get-available-slots");
const { db } = await import("@/lib/db");
const { organization } = await import("@/lib/db/schema/auth-schema");
const { availabilityRules } = await import(
  "@/lib/db/schema/availability-schema"
);
const { bookings } = await import("@/lib/db/schema/booking-schema");
const { businessProfiles } = await import("@/lib/db/schema/business-schema");
const { services } = await import("@/lib/db/schema/service-schema");

type RescheduleInput = Parameters<typeof customerRescheduleCore>[0];

const ORG = "org_itest_reschedule_core";
const SLUG = "itest-reschedule-core";
const ALI = `telegram:${"a".repeat(26)}`;
const VELI = `telegram:${"b".repeat(26)}`;
const tomorrow = new Date(Date.now() + 86_400_000);
let business: RescheduleInput["business"];
let serviceId: string;

function at(hourUTC: number, minute = 0): Date {
  const d = new Date(tomorrow);
  d.setUTCHours(hourUTC, minute, 0, 0);
  return d;
}

/** Ali's confirmed hour-long booking at `hour`, unless overridden. */
async function seedBooking(
  hour: number,
  overrides: Partial<typeof bookings.$inferInsert> = {}
) {
  const [row] = await db
    .insert(bookings)
    .values({
      organizationId: ORG,
      serviceId,
      customerName: "Ali Yılmaz",
      customerEmail: "ali@test.dev",
      startsAt: at(hour),
      endsAt: at(hour + 1),
      status: "confirmed",
      source: "assistant",
      customerRef: ALI,
      ...overrides,
    })
    .returning();
  return row;
}

const move = (bookingId: string, hour: number, minute = 0, customerRef = ALI) =>
  customerRescheduleCore({
    business,
    bookingId,
    customerRef,
    newStartsAt: at(hour, minute),
  });

const reload = (id: string) =>
  db.query.bookings.findFirst({ where: eq(bookings.id, id) });

beforeAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db
    .insert(organization)
    .values({ id: ORG, name: ORG, slug: SLUG, createdAt: new Date() });
  await db.insert(businessProfiles).values({
    organizationId: ORG,
    timezone: "UTC",
    minLeadTimeMinutes: 0,
  });
  const [service] = await db
    .insert(services)
    .values({
      organizationId: ORG,
      name: "Cilt Bakımı",
      durationMinutes: 60,
      priceCents: 150_000,
    })
    .returning({ id: services.id });
  serviceId = service.id;
  await db.insert(availabilityRules).values({
    organizationId: ORG,
    weekday: tomorrow.getUTCDay(),
    startMinutes: 9 * 60,
    endMinutes: 17 * 60,
  });
  const found = await getBusinessBySlug(SLUG);
  if (!found) throw new Error("seeded business not found");
  business = found;
});

beforeEach(async () => {
  await db.delete(bookings).where(eq(bookings.organizationId, ORG));
  rescheduledMock.mockReset();
});

afterAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db.$client.end();
});

describe("customerRescheduleCore", () => {
  it("moves the customer's booking to a free time, keeps its length, and tells them", async () => {
    const booking = await seedBooking(9);

    const result = await move(booking.id, 13);

    expect(result.ok).toBe(true);
    const after = await reload(booking.id);
    expect(after?.startsAt.toISOString()).toBe(at(13).toISOString());
    expect(after?.endsAt.toISOString()).toBe(at(14).toISOString());
    expect(rescheduledMock).toHaveBeenCalledWith(
      expect.objectContaining({ id: booking.id }),
      at(9)
    );
  });

  it("moves onto a time that overlaps its own", async () => {
    const booking = await seedBooking(10);

    expect((await move(booking.id, 10, 30)).ok).toBe(true);
  });

  it("refuses a time someone else holds, and changes nothing", async () => {
    await seedBooking(15, { customerRef: VELI });
    const booking = await seedBooking(9);

    expect(await move(booking.id, 15)).toMatchObject({
      ok: false,
      code: "slot_unavailable",
    });
    expect((await reload(booking.id))?.startsAt.toISOString()).toBe(
      at(9).toISOString()
    );
  });

  it("refuses a time off the slot grid", async () => {
    const booking = await seedBooking(9);

    expect(await move(booking.id, 11, 15)).toMatchObject({
      code: "slot_unavailable",
    });
  });

  it("refuses another customer's booking, even with its real id", async () => {
    const theirs = await seedBooking(9, { customerRef: VELI });

    expect(await move(theirs.id, 13)).toMatchObject({ code: "not_found" });
  });

  it("will not move a booking awaiting its kapora, or a cancelled one", async () => {
    const held = await seedBooking(9, {
      status: "pending",
      depositCents: 30_000,
      expiresAt: new Date(Date.now() + 20 * 60_000),
    });
    const cancelled = await seedBooking(11, {
      status: "cancelled",
      cancelledAt: new Date(),
    });

    expect(await move(held.id, 13)).toMatchObject({ code: "pending" });
    expect(await move(cancelled.id, 14)).toMatchObject({
      code: "already_cancelled",
    });
  });

  it("lets exactly one of two simultaneous moves into one slot through", async () => {
    const first = await seedBooking(9);
    const second = await seedBooking(11);

    const outcomes = await Promise.all([
      move(first.id, 14),
      move(second.id, 14),
    ]);
    const codes = outcomes.map((r) => (r.ok ? "ok" : r.code)).sort();

    expect(codes[0]).toBe("ok");
    expect(["slot_taken", "slot_unavailable"]).toContain(codes[1]);
    const atTwo = await db.query.bookings.findMany({
      where: and(
        eq(bookings.organizationId, ORG),
        eq(bookings.startsAt, at(14))
      ),
    });
    expect(atTwo).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/booking/customer-reschedule-core.integration.test.ts`

Expected: FAIL. `./customer-reschedule-core` cannot be resolved.

- [ ] **Step 3: Implement**

Create `lib/booking/customer-reschedule-core.ts`:

```ts
import "server-only";
import { and, eq, gt, sql } from "drizzle-orm";
import { getBookingForPage } from "@/lib/booking/confirmation";
import {
  type BusinessContext,
  getAvailableSlots,
  localDateISO,
} from "@/lib/booking/get-available-slots";
import { db } from "@/lib/db";
import { isExclusionConflict } from "@/lib/db/errors";
import { bookings } from "@/lib/db/schema/booking-schema";
import { sendBookingRescheduledEmail } from "@/lib/email/booking-notifications";

type Booking = typeof bookings.$inferSelect;

export type CustomerRescheduleCoreInput = {
  business: BusinessContext;
  bookingId: string;
  /** The assistant's customer: the booking must be theirs. */
  customerRef: string;
  newStartsAt: Date;
  now?: Date;
};

export type CustomerRescheduleErrorCode =
  | "not_found"
  | "already_cancelled"
  | "pending"
  | "started"
  | "slot_unavailable"
  | "slot_taken";

export type CustomerRescheduleCoreResult =
  | { ok: true; booking: Booking; previousStartsAt: Date }
  | { ok: false; code: CustomerRescheduleErrorCode; message: string };

function fail(
  code: CustomerRescheduleErrorCode,
  message: string
): CustomerRescheduleCoreResult {
  return { ok: false, code, message };
}

/**
 * A chat customer moves their own booking, keeping its length.
 *
 * Unlike the owner (rescheduleBooking), a customer is held to the slot grid:
 * the new time must be one the booking page would offer, with the booking's
 * own current time left out of the busy times so that a move overlapping it
 * is not refused by itself. A booking awaiting its kapora stays where it is —
 * its hold and its payment page both belong to the time the customer chose.
 * The exclusion constraint decides a race, on an UPDATE as on an INSERT.
 */
export async function customerRescheduleCore(
  input: CustomerRescheduleCoreInput
): Promise<CustomerRescheduleCoreResult> {
  const now = input.now ?? new Date();
  const { business, newStartsAt } = input;

  const found = await getBookingForPage(business.organizationId, input.bookingId);
  if (!found || found.booking.customerRef !== input.customerRef) {
    return fail("not_found", "Randevu bulunamadı.");
  }
  const { booking } = found;

  if (booking.status === "cancelled") {
    return fail("already_cancelled", "İptal edilmiş bir randevu taşınamaz.");
  }
  if (booking.status === "pending") {
    return fail("pending", "Kaporası beklenen bir randevu taşınamaz.");
  }
  if (booking.startsAt <= now) {
    return fail("started", "Başlamış bir randevu taşınamaz.");
  }
  if (newStartsAt.getTime() === booking.startsAt.getTime()) {
    return { ok: true, booking, previousStartsAt: booking.startsAt };
  }

  const dateISO = localDateISO(newStartsAt, business.profile.timezone);
  const slots = await getAvailableSlots(
    business,
    booking.serviceId,
    dateISO,
    now,
    { excludeBookingId: booking.id }
  );
  if (!slots?.some((slot) => slot.getTime() === newStartsAt.getTime())) {
    return fail(
      "slot_unavailable",
      "Bu saat artık müsait değil — lütfen başka bir saat seçin."
    );
  }

  const endsAt = new Date(
    newStartsAt.getTime() +
      (booking.endsAt.getTime() - booking.startsAt.getTime())
  );

  let moved: Booking | undefined;
  try {
    [moved] = await db
      .update(bookings)
      .set({ startsAt: newStartsAt, endsAt })
      .where(
        and(
          eq(bookings.id, booking.id),
          eq(bookings.organizationId, business.organizationId),
          eq(bookings.status, "confirmed"),
          gt(bookings.startsAt, sql`now()`)
        )
      )
      .returning();
  } catch (err) {
    if (isExclusionConflict(err)) {
      return fail(
        "slot_taken",
        "Bu saat az önce doldu — lütfen başka bir saat seçin."
      );
    }
    throw err;
  }
  // Cancelled, or begun, between the read and the write.
  if (!moved) return fail("not_found", "Randevu bulunamadı.");

  await sendBookingRescheduledEmail(moved, booking.startsAt);
  return { ok: true, booking: moved, previousStartsAt: booking.startsAt };
}
```

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/booking/customer-reschedule-core.integration.test.ts`

Expected: PASS (7 tests).

- [ ] **Step 5: Types, lint, commit**

```bash
bun run typecheck
bun run check:fix && bun run check
git add lib/booking/customer-reschedule-core.ts lib/booking/customer-reschedule-core.integration.test.ts
git commit -m "feat: let a customer move their own booking, held to the slot grid" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Who is asking — the assistant's authentication

**Files:**
- Modify: `package.json` and `bun.lock` (three packages)
- Create: `lib/security/bearer.ts`
- Modify: `lib/billing/cron-auth.ts` (delegates to `checkBearer`)
- Create: `lib/assistant/identity.ts`
- Create: `lib/assistant/request-auth.ts`
- Create: `lib/assistant/request-auth.integration.test.ts`
- Modify: `.env.example` (`ASSISTANT_MCP_TOKEN`)

**Interfaces:**
- Consumes: `getBusinessBySlug` and `BusinessContext` (existing); `businessProfiles.assistantEnabled` (Task 1).
- Produces:
  ```ts
  // lib/security/bearer.ts
  export type BearerCheck = "ok" | "unauthorized" | "misconfigured";
  export function checkBearer(authorizationHeader: string | null, secret: string | undefined): BearerCheck;
  // lib/assistant/identity.ts
  export const BUSINESS_HEADER = "x-rezerve-business";
  export const CUSTOMER_HEADER = "x-rezerve-customer";
  export function isCustomerRef(value: string): boolean;
  export type AssistantIdentity = { business: BusinessContext; customerRef: string };
  export function identityFrom(authInfo: AuthInfo | undefined): AssistantIdentity; // throws without one
  // lib/assistant/request-auth.ts
  export const ASSISTANT_CLIENT_ID = "rezerve-asistan";
  export type AssistantAuth = { ok: true; authInfo: AuthInfo } | { ok: false; response: Response };
  export function authenticateAssistant(req: Request, secret: string | undefined): Promise<AssistantAuth>;
  ```
  `AuthInfo` comes from `@modelcontextprotocol/server`. Its `extra` is `{ identity: AssistantIdentity }`.

- [ ] **Step 1: Install the MCP packages**

```bash
bun add mcp-handler@2.3.0 @modelcontextprotocol/server@2.3.1
bun add -d @modelcontextprotocol/client@2.3.1
```

Expected: `package.json` lists `"mcp-handler": "2.3.0"` and `"@modelcontextprotocol/server": "2.3.1"` under dependencies, and `"@modelcontextprotocol/client": "2.3.1"` under devDependencies. Pin them without `^` (edit `package.json` if bun wrote carets, then run `bun install`). `bun.lock` is updated.

- [ ] **Step 2: Write the failing test**

Create `lib/assistant/request-auth.integration.test.ts`:

```ts
/**
 * The door in front of /api/mcp. The assistant proves itself with a shared
 * token, and names the business and the customer in headers — the only
 * place they can come from, since no tool takes them as arguments.
 */
import { eq } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { db } from "@/lib/db";
import { organization } from "@/lib/db/schema/auth-schema";
import { businessProfiles } from "@/lib/db/schema/business-schema";
import { identityFrom } from "./identity";
import { authenticateAssistant } from "./request-auth";

const ON = { id: "org_itest_mcp_auth_on", slug: "itest-mcp-auth-on" };
const OFF = { id: "org_itest_mcp_auth_off", slug: "itest-mcp-auth-off" };
const SECRET = "itest-assistant-token";
const ALI = `telegram:${"a".repeat(26)}`;

beforeAll(async () => {
  for (const org of [ON, OFF]) {
    await db.delete(organization).where(eq(organization.id, org.id));
    await db.insert(organization).values({
      id: org.id,
      name: org.id,
      slug: org.slug,
      createdAt: new Date(),
    });
    await db.insert(businessProfiles).values({
      organizationId: org.id,
      timezone: "Europe/Istanbul",
      assistantEnabled: org === ON,
    });
  }
});

afterAll(async () => {
  for (const org of [ON, OFF]) {
    await db.delete(organization).where(eq(organization.id, org.id));
  }
  await db.$client.end();
});

const valid = {
  authorization: `Bearer ${SECRET}`,
  "x-rezerve-business": ON.slug,
  "x-rezerve-customer": ALI,
};

const request = (headers: Record<string, string>) =>
  new Request("http://localhost:3000/api/mcp", { method: "POST", headers });

async function statusOf(
  headers: Record<string, string>,
  secret: string | undefined = SECRET
) {
  const auth = await authenticateAssistant(request(headers), secret);
  return auth.ok ? 200 : auth.response.status;
}

describe("authenticateAssistant", () => {
  it("refuses everyone while the token is not configured", async () => {
    expect(await statusOf(valid, undefined)).toBe(503);
    expect(await statusOf(valid, "")).toBe(503);
  });

  it("refuses a missing or wrong token", async () => {
    expect(
      await statusOf({
        "x-rezerve-business": ON.slug,
        "x-rezerve-customer": ALI,
      })
    ).toBe(401);
    expect(
      await statusOf({ ...valid, authorization: "Bearer not-the-token" })
    ).toBe(401);
  });

  it("takes the customer ref exactly as the assistant writes it, or not at all", async () => {
    for (const ref of [
      "",
      `telegram:${"A".repeat(26)}`,
      `telegram:${"a".repeat(25)}`,
      `sms:${"a".repeat(26)}`,
      `telegram:${"a".repeat(25)}1`,
    ]) {
      expect(await statusOf({ ...valid, "x-rezerve-customer": ref })).toBe(
        400
      );
    }
  });

  it("answers an unknown business and a switched-off one alike", async () => {
    expect(
      await statusOf({ ...valid, "x-rezerve-business": "itest-no-such-shop" })
    ).toBe(403);
    expect(await statusOf({ ...valid, "x-rezerve-business": OFF.slug })).toBe(
      403
    );
  });

  it("hands the tools the business and the customer", async () => {
    const auth = await authenticateAssistant(request(valid), SECRET);
    if (!auth.ok) throw new Error(`refused: ${auth.response.status}`);

    const identity = identityFrom(auth.authInfo);
    expect(identity.business.slug).toBe(ON.slug);
    expect(identity.business.organizationId).toBe(ON.id);
    expect(identity.customerRef).toBe(ALI);
  });

  it("never lets a tool run as nobody", () => {
    expect(() => identityFrom(undefined)).toThrow(
      /without an authenticated identity/
    );
  });
});
```

- [ ] **Step 3: Run it to see it fail**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/assistant/request-auth.integration.test.ts`

Expected: FAIL. `./identity` and `./request-auth` cannot be resolved.

- [ ] **Step 4: Extract the bearer check**

Create `lib/security/bearer.ts`:

```ts
import { timingSafeEqual } from "node:crypto";

/**
 * A shared-secret bearer check — the renewal cron's, and the chat assistant's
 * on /api/mcp.
 *
 * Fails closed on a missing secret. An unset secret is a deployment mistake,
 * and the dangerous reading of it — "no secret configured, so let everyone
 * through" — is exactly the one to rule out.
 */
export type BearerCheck = "ok" | "unauthorized" | "misconfigured";

function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  // timingSafeEqual throws on length mismatch, which would itself leak length.
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

export function checkBearer(
  authorizationHeader: string | null,
  secret: string | undefined
): BearerCheck {
  if (!secret) return "misconfigured";
  if (!authorizationHeader) return "unauthorized";
  return safeEqual(authorizationHeader, `Bearer ${secret}`)
    ? "ok"
    : "unauthorized";
}
```

Replace `lib/billing/cron-auth.ts` with:

```ts
import { type BearerCheck, checkBearer } from "@/lib/security/bearer";

/**
 * Bearer-token check for the renewal cron.
 *
 * Lives here rather than in the route so it can be tested: this is the only
 * thing standing between the open internet and a job that charges cards. The
 * check itself — fail-closed, constant-time — is shared with the assistant's
 * endpoint, in lib/security/bearer.ts.
 */
export type CronAuth = BearerCheck;

export function checkCronAuth(
  authorizationHeader: string | null,
  secret: string | undefined
): CronAuth {
  return checkBearer(authorizationHeader, secret);
}
```

- [ ] **Step 5: Implement identity and the door**

Create `lib/assistant/identity.ts`:

```ts
import type { AuthInfo } from "@modelcontextprotocol/server";
import type { BusinessContext } from "@/lib/booking/get-available-slots";

/**
 * Who the chat assistant is acting for on a request to /api/mcp.
 *
 * Both halves arrive as headers the assistant sets — never as tool
 * arguments, which the model writes. However the model is talked to, it
 * cannot name another business or another customer.
 */
export const BUSINESS_HEADER = "x-rezerve-business";
export const CUSTOMER_HEADER = "x-rezerve-customer";

/**
 * `<channel>:<26 lowercase base32 characters>` — an HMAC of the channel's
 * user id that only the assistant can compute. Never normalised: two
 * spellings of one person would split their bookings in two.
 */
const CUSTOMER_REF = /^(web|telegram|whatsapp|eval):[a-z2-7]{26}$/;

export function isCustomerRef(value: string): boolean {
  return CUSTOMER_REF.test(value);
}

export type AssistantIdentity = {
  business: BusinessContext;
  customerRef: string;
};

/** The identity request-auth.ts attached, as a tool handler receives it. */
export function identityFrom(
  authInfo: AuthInfo | undefined
): AssistantIdentity {
  const identity = authInfo?.extra?.identity as AssistantIdentity | undefined;
  if (!identity) {
    // Unreachable through the route, which authenticates first — but a tool
    // must never run as nobody, so it fails loudly rather than guessing.
    throw new Error("Assistant tool called without an authenticated identity");
  }
  return identity;
}
```

Create `lib/assistant/request-auth.ts`:

```ts
import "server-only";
import type { AuthInfo } from "@modelcontextprotocol/server";
import { getBusinessBySlug } from "@/lib/booking/get-available-slots";
import { checkBearer } from "@/lib/security/bearer";
import {
  type AssistantIdentity,
  BUSINESS_HEADER,
  CUSTOMER_HEADER,
  isCustomerRef,
} from "./identity";

export const ASSISTANT_CLIENT_ID = "rezerve-asistan";

export type AssistantAuth =
  | { ok: true; authInfo: AuthInfo }
  | { ok: false; response: Response };

function deny(status: number, error: string): AssistantAuth {
  return { ok: false, response: Response.json({ error }, { status }) };
}

/**
 * The door in front of /api/mcp, checked on every request.
 *
 * One service holds the token — rezerve-asistan — so this is a shared secret
 * rather than OAuth: there is no third party to delegate to. What the token
 * does not settle, the headers do: which business, and which of its chat
 * customers. Both are checked here, before any tool can run.
 */
export async function authenticateAssistant(
  req: Request,
  secret: string | undefined
): Promise<AssistantAuth> {
  const bearer = checkBearer(req.headers.get("authorization"), secret);
  if (bearer === "misconfigured") {
    console.error("ASSISTANT_MCP_TOKEN is not set; /api/mcp refuses everyone");
    return deny(503, "misconfigured");
  }
  if (bearer === "unauthorized") return deny(401, "unauthorized");

  const customerRef = req.headers.get(CUSTOMER_HEADER) ?? "";
  if (!isCustomerRef(customerRef)) return deny(400, "invalid_customer");

  const slug = req.headers.get(BUSINESS_HEADER) ?? "";
  const business = slug ? await getBusinessBySlug(slug) : null;
  // One answer for "no such business" and "not switched on": the token holder
  // learns nothing about which businesses exist.
  if (!business?.profile.assistantEnabled) return deny(403, "forbidden");

  const identity: AssistantIdentity = { business, customerRef };
  return {
    ok: true,
    authInfo: {
      // The secret itself never travels further than this check.
      token: "service",
      clientId: ASSISTANT_CLIENT_ID,
      scopes: ["assistant"],
      extra: { identity },
    },
  };
}
```

- [ ] **Step 6: Document the token**

Append to `.env.example`:

```bash

# Shared secret the chat assistant (rezerve-asistan) sends as a bearer token
# on /api/mcp. Unset, the endpoint refuses every request — it fails closed.
# Generate with: openssl rand -base64 32
ASSISTANT_MCP_TOKEN=
```

- [ ] **Step 7: Run the tests**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/assistant/request-auth.integration.test.ts lib/billing/cron-auth.test.ts
```

Expected: PASS (6 new tests, plus the existing cron-auth tests unchanged).

- [ ] **Step 8: Types, lint, commit**

```bash
bun run typecheck
bun run check:fix && bun run check
git add package.json bun.lock lib/security/bearer.ts lib/billing/cron-auth.ts lib/assistant/identity.ts lib/assistant/request-auth.ts lib/assistant/request-auth.integration.test.ts .env.example
git commit -m "feat: authenticate the assistant, with identity in headers only" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The read tools, and the contract they answer to

**Files:**
- Create: `lib/assistant/tool-result.ts`
- Create: `lib/assistant/format.ts`
- Create: `lib/assistant/format.test.ts`
- Create: `lib/assistant/tool-definitions.ts` (the read half)
- Create: `lib/assistant/read-tools.ts`
- Create: `lib/assistant/read-tools.integration.test.ts`

**Interfaces:**
- Consumes: `AssistantIdentity` (Task 7); `getAvailableSlots` and `localDateISO` (existing); `getBookingForPage` (existing); `getBilling` (existing).
- Produces:
  ```ts
  // tool-result.ts
  export type ToolError = { code: string; message: string };
  export type ToolResult<T> = { ok: true; data: T } | { ok: false; error: ToolError };
  export function toolError(code: string, message: string): { ok: false; error: ToolError };
  // format.ts
  export function localTime(instant: Date, timezone: string): string;   // "15:30"
  export function localLabel(instant: Date, timezone: string): string;  // "Cumartesi 17 Ekim 15:30"
  // tool-definitions.ts
  export const READ_TOOLS: { get_business_info, list_services, find_slots, list_my_bookings, get_booking };
  //   each: { title, description, input: ZodObject, output: ZodObject, annotations }
  export type BusinessInfo, ServiceInfo, Slot, BookingView,
    ListServicesOutput, FindSlotsInput, FindSlotsOutput, ListMyBookingsOutput, GetBookingInput;
  // read-tools.ts
  export function getBusinessInfo(identity: AssistantIdentity, now?: Date): Promise<ToolResult<BusinessInfo>>;
  export function listServices(identity: AssistantIdentity): Promise<ToolResult<ListServicesOutput>>;
  export function findSlots(identity: AssistantIdentity, args: FindSlotsInput, now?: Date): Promise<ToolResult<FindSlotsOutput>>;
  export function listMyBookings(identity: AssistantIdentity, now?: Date): Promise<ToolResult<ListMyBookingsOutput>>;
  export function getBooking(identity: AssistantIdentity, args: GetBookingInput): Promise<ToolResult<BookingView>>;
  ```

- [ ] **Step 1: Write the failing formatting test**

Create `lib/assistant/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { localLabel, localTime } from "./format";

describe("localLabel", () => {
  it("says a time the way a person reads it back, in the business's timezone", () => {
    expect(localLabel(new Date("2026-10-17T12:30:00Z"), "Europe/Istanbul")).toBe(
      "Cumartesi 17 Ekim 15:30"
    );
  });

  it("puts midnight on the business's new day, as 00:00", () => {
    expect(localLabel(new Date("2026-10-16T21:00:00Z"), "Europe/Istanbul")).toBe(
      "Cumartesi 17 Ekim 00:00"
    );
  });

  it("follows the clock across a DST change", () => {
    // New York springs forward at 02:00 on 8 March 2026.
    expect(localLabel(new Date("2026-03-08T07:30:00Z"), "America/New_York")).toBe(
      "Pazar 8 Mart 03:30"
    );
  });
});

describe("localTime", () => {
  it("pads the hour", () => {
    expect(localTime(new Date("2026-10-17T06:05:00Z"), "Europe/Istanbul")).toBe(
      "09:05"
    );
  });
});
```

Run: `bun run test lib/assistant/format.test.ts`. It needs no database.

Expected: FAIL. `./format` cannot be resolved.

- [ ] **Step 2: Implement formatting**

Create `lib/assistant/format.ts`:

```ts
function parts(
  instant: Date,
  timezone: string,
  options: Intl.DateTimeFormatOptions
): Intl.DateTimeFormatPart[] {
  return new Intl.DateTimeFormat("tr-TR", {
    timeZone: timezone,
    ...options,
  }).formatToParts(instant);
}

function pick(
  list: Intl.DateTimeFormatPart[],
  type: Intl.DateTimeFormatPartTypes
): string {
  return list.find((part) => part.type === type)?.value ?? "";
}

/**
 * "15:30" — an instant's wall-clock time in the business's timezone. h23, so
 * midnight is 00:00 and never 24:00.
 */
export function localTime(instant: Date, timezone: string): string {
  const list = parts(instant, timezone, {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return `${pick(list, "hour")}:${pick(list, "minute")}`;
}

/**
 * "Cumartesi 17 Ekim 15:30" — how the assistant reads a time back to a
 * customer: weekday first, as people say it, in the business's timezone.
 * Built from parts, so it never depends on a locale's word order.
 */
export function localLabel(instant: Date, timezone: string): string {
  const list = parts(instant, timezone, {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  return `${pick(list, "weekday")} ${pick(list, "day")} ${pick(list, "month")} ${localTime(instant, timezone)}`;
}
```

Run: `bun run test lib/assistant/format.test.ts`. Expected: PASS (4 tests).

- [ ] **Step 3: The tool result type and the read half of the contract**

Create `lib/assistant/tool-result.ts`:

```ts
/**
 * What a tool function returns: its data, or a refusal with a stable code the
 * assistant can branch on and a Turkish message it can pass along.
 */
export type ToolError = { code: string; message: string };

export type ToolResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: ToolError };

export function toolError(
  code: string,
  message: string
): { ok: false; error: ToolError } {
  return { ok: false, error: { code, message } };
}
```

Create `lib/assistant/tool-definitions.ts`:

```ts
import { z } from "zod";

/**
 * The assistant's tools as schemas — the contract between Rezerve and the
 * rezerve-asistan service. /api/mcp registers them, `bun run mcp:schema`
 * prints them as JSON Schema for the other repository's contract test, and
 * the tool functions return exactly these shapes.
 *
 * No tool takes a business or a customer: those arrive as headers the
 * assistant sets (lib/assistant/request-auth.ts), never as arguments the
 * model writes. Field names are snake_case, as MCP tools conventionally are.
 */

/** A business-local calendar date, "2026-10-17". */
const localDate = z.iso.date();
/** A business-local wall-clock time, "15:30". */
const wallTime = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/);
/** An instant — accepted with an offset as well as in UTC. */
const instant = z.iso.datetime({ offset: true });
/** A booking id as the model may pass it back; a bad one is "not found". */
const bookingId = z.string().max(64);

const businessInfo = z.object({
  name: z.string(),
  timezone: z.string(),
  today: localDate,
  now_local: z.string(),
  phone: z.string().nullable(),
  address: z.string().nullable(),
  description: z.string().nullable(),
  assistant_notes: z.string().nullable(),
  booking_window_days: z.number().int(),
  min_lead_time_minutes: z.number().int(),
  refund_notice_hours: z.number().int(),
  booking_page_url: z.url().nullable(),
});

const serviceInfo = z.object({
  id: z.uuid(),
  name: z.string(),
  description: z.string().nullable(),
  duration_minutes: z.number().int(),
  price_cents: z.number().int(),
  deposit_cents: z.number().int().nullable(),
  currency: z.string(),
});

const slot = z.object({ starts_at: instant, local_label: z.string() });

const bookingView = z.object({
  id: z.uuid(),
  service_name: z.string(),
  starts_at: instant,
  local_label: z.string(),
  status: z.enum(["pending", "confirmed", "cancelled"]),
  deposit_cents: z.number().int().nullable(),
  paid: z.boolean(),
  hold_expires_at: instant.nullable(),
  cancelled_at: instant.nullable(),
  cancelled_by: z.enum(["owner", "customer"]).nullable(),
});

const findSlotsInput = z.object({
  service_id: z.uuid(),
  from_date: localDate,
  to_date: localDate,
  earliest_time: wallTime.optional(),
  latest_time: wallTime.optional(),
  limit: z.number().int().min(1).max(20).optional(),
});

const READ_ONLY = { readOnlyHint: true, openWorldHint: false } as const;

export const READ_TOOLS = {
  get_business_info: {
    title: "İşletme bilgisi",
    description:
      "The business's name, contact details, the owner's notes, its timezone with today's date there, its booking window and the kapora refund rule. Call it before talking about dates.",
    input: z.object({}),
    output: businessInfo,
    annotations: READ_ONLY,
  },
  list_services: {
    title: "Hizmetler",
    description:
      "Active services with duration and price. deposit_cents is the kapora that will actually be collected, or null for none.",
    input: z.object({}),
    output: z.object({ services: z.array(serviceInfo) }),
    annotations: READ_ONLY,
  },
  find_slots: {
    title: "Boş saatler",
    description:
      "Free start times for one service between two business-local dates (at most 14 days), optionally between two wall-clock times. Returns at most `limit` (default 10); truncated is true when there were more.",
    input: findSlotsInput,
    output: z.object({ slots: z.array(slot), truncated: z.boolean() }),
    annotations: READ_ONLY,
  },
  list_my_bookings: {
    title: "Randevularım",
    description:
      "This customer's upcoming bookings: confirmed, and holds still waiting for their kapora.",
    input: z.object({}),
    output: z.object({ bookings: z.array(bookingView) }),
    annotations: READ_ONLY,
  },
  get_booking: {
    title: "Randevu",
    description:
      "One of this customer's bookings by id — for example to see whether its kapora has been paid.",
    input: z.object({ booking_id: bookingId }),
    output: bookingView,
    annotations: READ_ONLY,
  },
};

export type BusinessInfo = z.infer<typeof businessInfo>;
export type ServiceInfo = z.infer<typeof serviceInfo>;
export type Slot = z.infer<typeof slot>;
export type BookingView = z.infer<typeof bookingView>;
export type ListServicesOutput = z.infer<
  typeof READ_TOOLS.list_services.output
>;
export type FindSlotsInput = z.infer<typeof findSlotsInput>;
export type FindSlotsOutput = z.infer<typeof READ_TOOLS.find_slots.output>;
export type ListMyBookingsOutput = z.infer<
  typeof READ_TOOLS.list_my_bookings.output
>;
export type GetBookingInput = z.infer<typeof READ_TOOLS.get_booking.input>;
```

- [ ] **Step 4: Write the failing read-tools test**

Create `lib/assistant/read-tools.integration.test.ts`:

```ts
/**
 * The assistant's read tools against a real database: what a business tells
 * a chat customer, which times it offers, and which bookings it shows them.
 * The business is in Istanbul, so every date and label is checked in its own
 * timezone rather than in UTC.
 */
import { eq } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { localDateISO } from "@/lib/booking/get-available-slots";
import { localInstant } from "@/lib/booking/opening-hours";
import { db } from "@/lib/db";
import { organization } from "@/lib/db/schema/auth-schema";
import { availabilityRules } from "@/lib/db/schema/availability-schema";
import {
  orgPayoutAccounts,
  orgSubscriptions,
} from "@/lib/db/schema/billing-schema";
import { bookings } from "@/lib/db/schema/booking-schema";
import { businessProfiles } from "@/lib/db/schema/business-schema";
import { services } from "@/lib/db/schema/service-schema";
import { getBusinessBySlug } from "@/lib/booking/get-available-slots";
import type { AssistantIdentity } from "./identity";
import {
  findSlots,
  getBooking,
  getBusinessInfo,
  listMyBookings,
  listServices,
} from "./read-tools";

const TZ = "Europe/Istanbul";
const PRO = { id: "org_itest_read_tools_pro", slug: "itest-read-tools-pro" };
const FREE = { id: "org_itest_read_tools_free", slug: "itest-read-tools-free" };
const ALI = `telegram:${"a".repeat(26)}`;
const VELI = `telegram:${"b".repeat(26)}`;
const DAY = 86_400_000;
/** Business-local dates: find_slots looks at TOMORROW, bookings sit later. */
const localDay = (offset: number) =>
  localDateISO(new Date(Date.now() + offset * DAY), TZ);
const TOMORROW = localDay(1);
const DAY_AFTER = localDay(2);
const at = (dateISO: string, hour: number) =>
  localInstant(dateISO, hour * 60, TZ);

let ali: AssistantIdentity;
let aliAtTheFreeShop: AssistantIdentity;
let facial: string;
let aliConfirmed: string;
let veliConfirmed: string;

async function seedOrg(org: { id: string; slug: string }) {
  await db.delete(organization).where(eq(organization.id, org.id));
  await db.insert(organization).values({
    id: org.id,
    name: org.id,
    slug: org.slug,
    createdAt: new Date(),
  });
  await db.insert(businessProfiles).values({
    organizationId: org.id,
    timezone: TZ,
    minLeadTimeMinutes: 0,
    phone: "0212 000 00 00",
    assistantEnabled: true,
    assistantNotes: "Otopark yok; en yakın otopark 100 metre ileride.",
  });
  // Open 09:00–12:00 every day: five hour-long starts on the 30-minute grid.
  await db.insert(availabilityRules).values(
    [0, 1, 2, 3, 4, 5, 6].map((weekday) => ({
      organizationId: org.id,
      weekday,
      startMinutes: 9 * 60,
      endMinutes: 12 * 60,
    }))
  );
}

async function identityFor(slug: string, customerRef: string) {
  const business = await getBusinessBySlug(slug);
  if (!business) throw new Error(`seeded business ${slug} not found`);
  return { business, customerRef };
}

beforeAll(async () => {
  await seedOrg(PRO);
  await seedOrg(FREE);

  const rows = await db
    .insert(services)
    .values([
      {
        organizationId: PRO.id,
        name: "Cilt Bakımı",
        durationMinutes: 60,
        priceCents: 150_000,
        depositCents: 30_000,
      },
      {
        organizationId: PRO.id,
        name: "Manikür",
        durationMinutes: 30,
        priceCents: 50_000,
      },
      {
        organizationId: PRO.id,
        name: "Eski Hizmet",
        durationMinutes: 30,
        priceCents: 10_000,
        active: false,
      },
      {
        organizationId: FREE.id,
        name: "Cilt Bakımı",
        durationMinutes: 60,
        priceCents: 150_000,
        depositCents: 30_000,
      },
    ])
    .returning({ id: services.id });
  facial = rows[0].id;

  // Pro with an approved submerchant: its kapora is really collected.
  await db.insert(orgSubscriptions).values({
    organizationId: PRO.id,
    plan: "pro",
    status: "active",
    currentPeriodEndsAt: new Date(Date.now() + 30 * DAY),
    lastSyncedAt: new Date(),
  });
  await db.insert(orgPayoutAccounts).values({
    organizationId: PRO.id,
    status: "active",
    subMerchantKey: "sub_merchant_itest_read_tools",
    subMerchantExternalId: PRO.id,
    merchantType: "personal",
    name: "ITest Pro",
    contactName: "Ada",
    contactSurname: "Lovelace",
    identityNumber: "10000000146",
    iban: "TR180006200119000006672315",
    address: "Kadıköy/İstanbul",
    gsmNumber: "+905350000000",
    email: "pro@test.dev",
  });

  // DAY_AFTER: Ali's confirmed booking, a live hold, an expired hold and a
  // cancelled one; yesterday, a past one; three days out, Veli's.
  const booking = (values: Partial<typeof bookings.$inferInsert>) => ({
    organizationId: PRO.id,
    serviceId: facial,
    customerName: "Ali Yılmaz",
    source: "assistant" as const,
    customerRef: ALI,
    status: "confirmed" as const,
    startsAt: at(DAY_AFTER, 9),
    endsAt: at(DAY_AFTER, 10),
    ...values,
  });
  const inserted = await db
    .insert(bookings)
    .values([
      booking({}),
      booking({
        status: "pending",
        depositCents: 30_000,
        expiresAt: new Date(Date.now() + 20 * 60_000),
        startsAt: at(DAY_AFTER, 10),
        endsAt: at(DAY_AFTER, 11),
      }),
      booking({
        status: "pending",
        depositCents: 30_000,
        expiresAt: new Date(Date.now() - 5 * 60_000),
        startsAt: at(DAY_AFTER, 11),
        endsAt: at(DAY_AFTER, 12),
      }),
      booking({ status: "cancelled", cancelledAt: new Date() }),
      booking({ startsAt: at(localDay(-1), 9), endsAt: at(localDay(-1), 10) }),
      booking({
        customerName: "Veli Kaya",
        customerRef: VELI,
        startsAt: at(localDay(3), 9),
        endsAt: at(localDay(3), 10),
      }),
    ])
    .returning({ id: bookings.id });
  aliConfirmed = inserted[0].id;
  veliConfirmed = inserted[5].id;

  ali = await identityFor(PRO.slug, ALI);
  aliAtTheFreeShop = await identityFor(FREE.slug, ALI);
});

afterAll(async () => {
  for (const org of [PRO, FREE]) {
    await db.delete(organization).where(eq(organization.id, org.id));
  }
  await db.$client.end();
});

function ok<T>(result: { ok: true; data: T } | { ok: false }): T {
  if (!result.ok) throw new Error(`expected data, got ${JSON.stringify(result)}`);
  return result.data;
}

describe("get_business_info", () => {
  it("dates 'today' in the business's timezone, even while UTC is still yesterday", async () => {
    // 00:30 in Istanbul is 21:30 the evening before in UTC.
    const info = ok(await getBusinessInfo(ali, new Date("2026-10-16T21:30:00Z")));

    expect(info.today).toBe("2026-10-17");
    expect(info.now_local).toBe("2026-10-17T00:30");
    expect(info.timezone).toBe(TZ);
    expect(info.refund_notice_hours).toBe(24);
    expect(info.assistant_notes).toMatch(/Otopark/);
    expect(info.booking_page_url).toMatch(/\/r\/itest-read-tools-pro$/);
  });
});

describe("list_services", () => {
  it("lists only active services, with the kapora that will really be collected", async () => {
    const { services: listed } = ok(await listServices(ali));

    expect(listed.map((s) => s.name).sort()).toEqual(["Cilt Bakımı", "Manikür"]);
    expect(listed.find((s) => s.name === "Cilt Bakımı")?.deposit_cents).toBe(
      30_000
    );
  });

  it("shows no kapora where the plan does not collect it", async () => {
    const { services: listed } = ok(await listServices(aliAtTheFreeShop));

    expect(listed[0].deposit_cents).toBeNull();
  });
});

describe("find_slots", () => {
  const find = (args: Record<string, unknown>) =>
    findSlots(ali, {
      service_id: facial,
      from_date: TOMORROW,
      to_date: TOMORROW,
      ...args,
    });
  const times = (result: Awaited<ReturnType<typeof find>>) =>
    ok(result).slots.map((s) => s.local_label.slice(-5));

  it("offers the day's free starts, labelled in the business's time", async () => {
    expect(times(await find({}))).toEqual([
      "09:00",
      "09:30",
      "10:00",
      "10:30",
      "11:00",
    ]);
  });

  it("keeps to the hours the customer asked about", async () => {
    expect(
      times(await find({ earliest_time: "10:00", latest_time: "10:30" }))
    ).toEqual(["10:00", "10:30"]);
  });

  it("stops at the limit and says there were more", async () => {
    const result = ok(await find({ to_date: DAY_AFTER, limit: 3 }));

    expect(result.slots).toHaveLength(3);
    expect(result.truncated).toBe(true);
  });

  it("refuses more than fourteen days, a range that ends before it starts, and an unknown service", async () => {
    expect(await find({ to_date: localDay(16) })).toMatchObject({
      ok: false,
      error: { code: "range_too_long" },
    });
    expect(await find({ from_date: DAY_AFTER })).toMatchObject({
      ok: false,
      error: { code: "invalid" },
    });
    expect(
      await find({ service_id: "00000000-0000-4000-8000-000000000000" })
    ).toMatchObject({ ok: false, error: { code: "not_found" } });
  });
});

describe("list_my_bookings", () => {
  it("shows this customer's upcoming bookings and live holds, and nobody else's", async () => {
    const { bookings: listed } = ok(await listMyBookings(ali));

    expect(listed.map((b) => [b.status, b.local_label.slice(-5)])).toEqual([
      ["confirmed", "09:00"],
      ["pending", "10:00"],
    ]);
    expect(listed[1].hold_expires_at).not.toBeNull();
  });
});

describe("get_booking", () => {
  it("shows the customer's own booking", async () => {
    const booking = ok(await getBooking(ali, { booking_id: aliConfirmed }));

    expect(booking.service_name).toBe("Cilt Bakımı");
    expect(booking.paid).toBe(false);
  });

  it("treats someone else's booking, or a cut-short id, as not there", async () => {
    expect(await getBooking(ali, { booking_id: veliConfirmed })).toMatchObject({
      ok: false,
      error: { code: "not_found" },
    });
    expect(await getBooking(ali, { booking_id: "1234-kesik" })).toMatchObject({
      ok: false,
      error: { code: "not_found" },
    });
  });
});
```

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/assistant/read-tools.integration.test.ts`

Expected: FAIL. `./read-tools` cannot be resolved.

- [ ] **Step 5: Implement the read tools**

Create `lib/assistant/read-tools.ts`:

```ts
import "server-only";
import { and, asc, eq, gt, or, sql } from "drizzle-orm";
import { getBilling } from "@/lib/billing/get-billing";
import { REFUND_NOTICE_HOURS } from "@/lib/booking/cancellation-policy";
import { getBookingForPage } from "@/lib/booking/confirmation";
import {
  getAvailableSlots,
  localDateISO,
} from "@/lib/booking/get-available-slots";
import { db } from "@/lib/db";
import { bookings } from "@/lib/db/schema/booking-schema";
import { services } from "@/lib/db/schema/service-schema";
import { localLabel, localTime } from "./format";
import type { AssistantIdentity } from "./identity";
import type {
  BookingView,
  BusinessInfo,
  FindSlotsInput,
  FindSlotsOutput,
  GetBookingInput,
  ListMyBookingsOutput,
  ListServicesOutput,
  Slot,
} from "./tool-definitions";
import { type ToolResult, toolError } from "./tool-result";

type Booking = typeof bookings.$inferSelect;

/** find_slots reads each day separately; two weeks is a dozen queries a day. */
const MAX_RANGE_DAYS = 14;
const DEFAULT_SLOT_LIMIT = 10;
/** A customer with more upcoming bookings than this calls the business. */
const MAX_LISTED_BOOKINGS = 20;

const DAY_MS = 86_400_000;
const dayNumber = (dateISO: string) => Date.parse(`${dateISO}T00:00:00Z`);
const addDays = (dateISO: string, days: number) =>
  new Date(dayNumber(dateISO) + days * DAY_MS).toISOString().slice(0, 10);

function bookingPageUrl(slug: string): string | null {
  try {
    return new URL(`/r/${slug}`, process.env.NEXT_PUBLIC_APP_URL).href;
  } catch {
    return null;
  }
}

function viewOf(
  booking: Booking,
  serviceName: string,
  timezone: string
): BookingView {
  return {
    id: booking.id,
    service_name: serviceName,
    starts_at: booking.startsAt.toISOString(),
    local_label: localLabel(booking.startsAt, timezone),
    status: booking.status,
    deposit_cents: booking.depositCents,
    paid: booking.paymentTransactionId !== null,
    hold_expires_at:
      booking.status === "pending" && booking.expiresAt
        ? booking.expiresAt.toISOString()
        : null,
    cancelled_at: booking.cancelledAt?.toISOString() ?? null,
    cancelled_by: booking.cancelledBy,
  };
}

export async function getBusinessInfo(
  { business }: AssistantIdentity,
  now: Date = new Date()
): Promise<ToolResult<BusinessInfo>> {
  const { profile } = business;
  const today = localDateISO(now, profile.timezone);
  return {
    ok: true,
    data: {
      name: business.orgName,
      timezone: profile.timezone,
      today,
      now_local: `${today}T${localTime(now, profile.timezone)}`,
      phone: profile.phone,
      address: profile.address,
      description: profile.description,
      assistant_notes: profile.assistantNotes,
      booking_window_days: profile.bookingWindowDays,
      min_lead_time_minutes: profile.minLeadTimeMinutes,
      refund_notice_hours: REFUND_NOTICE_HOURS,
      booking_page_url: bookingPageUrl(business.slug),
    },
  };
}

export async function listServices({
  business,
}: AssistantIdentity): Promise<ToolResult<ListServicesOutput>> {
  const [rows, billing] = await Promise.all([
    db.query.services.findMany({
      where: and(
        eq(services.organizationId, business.organizationId),
        eq(services.active, true)
      ),
      orderBy: [asc(services.name)],
    }),
    getBilling(business.organizationId),
  ]);
  return {
    ok: true,
    data: {
      services: rows.map((service) => ({
        id: service.id,
        name: service.name,
        description: service.description,
        duration_minutes: service.durationMinutes,
        price_cents: service.priceCents,
        // Only a kapora that will actually be collected: on a lapsed plan the
        // service keeps its stored amount, but create_booking charges none.
        deposit_cents:
          service.depositCents != null && billing.onlineDeposit
            ? service.depositCents
            : null,
        currency: business.profile.currency,
      })),
    },
  };
}

export async function findSlots(
  { business }: AssistantIdentity,
  args: FindSlotsInput,
  now: Date = new Date()
): Promise<ToolResult<FindSlotsOutput>> {
  const days = (dayNumber(args.to_date) - dayNumber(args.from_date)) / DAY_MS + 1;
  if (days < 1) {
    return toolError("invalid", "Bitiş tarihi başlangıç tarihinden önce olamaz.");
  }
  if (days > MAX_RANGE_DAYS) {
    return toolError(
      "range_too_long",
      "En fazla 14 günlük bir aralık sorulabilir."
    );
  }

  const service = await db.query.services.findFirst({
    where: and(
      eq(services.id, args.service_id),
      eq(services.organizationId, business.organizationId),
      eq(services.active, true)
    ),
  });
  if (!service) return toolError("not_found", "Hizmet bulunamadı");

  const { timezone } = business.profile;
  const limit = args.limit ?? DEFAULT_SLOT_LIMIT;
  const found: Slot[] = [];
  let truncated = false;
  for (let day = 0; day < days && !truncated; day += 1) {
    // Null for a date outside the booking window: simply nothing to offer.
    const starts =
      (await getAvailableSlots(
        business,
        service.id,
        addDays(args.from_date, day),
        now
      )) ?? [];
    for (const start of starts) {
      const time = localTime(start, timezone);
      if (args.earliest_time && time < args.earliest_time) continue;
      if (args.latest_time && time > args.latest_time) continue;
      if (found.length === limit) {
        truncated = true;
        break;
      }
      found.push({
        starts_at: start.toISOString(),
        local_label: localLabel(start, timezone),
      });
    }
  }
  return { ok: true, data: { slots: found, truncated } };
}

export async function listMyBookings(
  { business, customerRef }: AssistantIdentity,
  now: Date = new Date()
): Promise<ToolResult<ListMyBookingsOutput>> {
  const rows = await db
    .select({ booking: bookings, serviceName: services.name })
    .from(bookings)
    .innerJoin(services, eq(services.id, bookings.serviceId))
    .where(
      and(
        eq(bookings.organizationId, business.organizationId),
        eq(bookings.customerRef, customerRef),
        gt(bookings.startsAt, now),
        // Confirmed, or a hold still waiting for its kapora — the same rule
        // the slot lookup uses for what occupies a time.
        or(
          eq(bookings.status, "confirmed"),
          and(
            eq(bookings.status, "pending"),
            gt(bookings.expiresAt, sql`now()`)
          )
        )
      )
    )
    .orderBy(asc(bookings.startsAt))
    .limit(MAX_LISTED_BOOKINGS);
  return {
    ok: true,
    data: {
      bookings: rows.map((row) =>
        viewOf(row.booking, row.serviceName, business.profile.timezone)
      ),
    },
  };
}

export async function getBooking(
  { business, customerRef }: AssistantIdentity,
  args: GetBookingInput
): Promise<ToolResult<BookingView>> {
  const found = await getBookingForPage(
    business.organizationId,
    args.booking_id
  );
  if (!found || found.booking.customerRef !== customerRef) {
    return toolError("not_found", "Randevu bulunamadı.");
  }
  return {
    ok: true,
    data: viewOf(
      found.booking,
      found.service?.name ?? "",
      business.profile.timezone
    ),
  };
}
```

- [ ] **Step 6: Run the tests**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/assistant/read-tools.integration.test.ts lib/assistant/format.test.ts
```

Expected: PASS (10 read-tool tests, 4 format tests).

- [ ] **Step 7: Types, lint, commit**

```bash
bun run typecheck
bun run check:fix && bun run check
git add lib/assistant/tool-result.ts lib/assistant/format.ts lib/assistant/format.test.ts lib/assistant/tool-definitions.ts lib/assistant/read-tools.ts lib/assistant/read-tools.integration.test.ts
git commit -m "feat: the assistant's read tools, against a schema contract" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The write tools

**Files:**
- Modify: `lib/assistant/tool-definitions.ts` (append the write half and `TOOL_DEFINITIONS`)
- Create: `lib/assistant/write-tools.ts`
- Create: `lib/assistant/write-tools.integration.test.ts`

**Interfaces:**
- Consumes:
  - `createBookingCore` (Task 4), `customerCancelCore` (Task 5), `customerRescheduleCore` (Task 6);
  - `AssistantIdentity` (Task 7);
  - `localLabel`, `ToolResult` and `toolError`, and the `bookingId` and `instant` atoms (Task 8).
- Produces:
  ```ts
  export const WRITE_TOOLS: { create_booking, reschedule_booking, cancel_booking };
  export const TOOL_DEFINITIONS: typeof READ_TOOLS & typeof WRITE_TOOLS;
  export type CreateBookingInput, CreateBookingOutput, RescheduleBookingInput,
    RescheduleBookingOutput, CancelBookingInput, CancelBookingOutput;
  export function createBookingTool(identity: AssistantIdentity, args: CreateBookingInput): Promise<ToolResult<CreateBookingOutput>>;
  export function rescheduleBookingTool(identity: AssistantIdentity, args: RescheduleBookingInput): Promise<ToolResult<RescheduleBookingOutput>>;
  export function cancelBookingTool(identity: AssistantIdentity, args: CancelBookingInput): Promise<ToolResult<CancelBookingOutput>>;
  ```

- [ ] **Step 1: Write the failing test**

Create `lib/assistant/write-tools.integration.test.ts`:

```ts
/**
 * The assistant's write tools — thin over the booking cores, which are tested
 * on their own. What is tested here is the translation: the identity the
 * assistant acts for, a time sent with its offset, a phone that cannot be a
 * number, and refusals arriving as codes rather than exceptions.
 */
import { eq } from "drizzle-orm";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("next/headers", () => ({ headers: async () => new Map() }));
vi.mock("@/lib/email/booking-notifications", () => ({
  sendBookingConfirmedEmails: vi.fn(),
  sendBookingCancelledEmails: vi.fn(),
  sendBookingRescheduledEmail: vi.fn(),
}));
vi.mock("@/lib/payments", () => ({
  getPaymentProvider: () => ({
    createDepositCheckout: vi.fn(async () => ({
      token: `tok_${Math.random().toString(36).slice(2)}`,
      paymentPageUrl: "https://sandbox-cpp.iyzipay.com/checkout",
    })),
    refundTransaction: vi.fn(async () => ({
      refunded: true,
      errorMessage: null,
    })),
  }),
}));

const { cancelBookingTool, createBookingTool, rescheduleBookingTool } =
  await import("./write-tools");
const { getBusinessBySlug } = await import("@/lib/booking/get-available-slots");
const { db } = await import("@/lib/db");
const { organization } = await import("@/lib/db/schema/auth-schema");
const { availabilityRules } = await import(
  "@/lib/db/schema/availability-schema"
);
const { orgPayoutAccounts, orgSubscriptions } = await import(
  "@/lib/db/schema/billing-schema"
);
const { bookingAttempts, bookings } = await import(
  "@/lib/db/schema/booking-schema"
);
const { businessProfiles } = await import("@/lib/db/schema/business-schema");
const { services } = await import("@/lib/db/schema/service-schema");

type Identity = Parameters<typeof createBookingTool>[0];

const ORG = "org_itest_write_tools";
const SLUG = "itest-write-tools";
const ALI = `telegram:${"a".repeat(26)}`;
const VELI = `telegram:${"b".repeat(26)}`;
/** A UTC business, so a local date is simply the UTC date. */
const DAY = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
let ali: Identity;
let veli: Identity;
let haircut: string;
let facial: string;
let retired: string;

beforeAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db
    .insert(organization)
    .values({ id: ORG, name: ORG, slug: SLUG, createdAt: new Date() });
  await db.insert(businessProfiles).values({
    organizationId: ORG,
    timezone: "UTC",
    minLeadTimeMinutes: 0,
    assistantEnabled: true,
  });
  const rows = await db
    .insert(services)
    .values([
      {
        organizationId: ORG,
        name: "Saç Kesimi",
        durationMinutes: 60,
        priceCents: 80_000,
      },
      {
        organizationId: ORG,
        name: "Cilt Bakımı",
        durationMinutes: 60,
        priceCents: 150_000,
        depositCents: 30_000,
      },
      {
        organizationId: ORG,
        name: "Eski Hizmet",
        durationMinutes: 60,
        priceCents: 10_000,
        active: false,
      },
    ])
    .returning({ id: services.id });
  [haircut, facial, retired] = rows.map((row) => row.id);
  await db.insert(availabilityRules).values({
    organizationId: ORG,
    weekday: new Date(`${DAY}T12:00:00Z`).getUTCDay(),
    startMinutes: 9 * 60,
    endMinutes: 17 * 60,
  });
  await db.insert(orgSubscriptions).values({
    organizationId: ORG,
    plan: "pro",
    status: "active",
    currentPeriodEndsAt: new Date(Date.now() + 30 * 86_400_000),
    lastSyncedAt: new Date(),
  });
  await db.insert(orgPayoutAccounts).values({
    organizationId: ORG,
    status: "active",
    subMerchantKey: "sub_merchant_itest_write_tools",
    subMerchantExternalId: ORG,
    merchantType: "personal",
    name: "ITest Pro",
    contactName: "Ada",
    contactSurname: "Lovelace",
    identityNumber: "10000000146",
    iban: "TR180006200119000006672315",
    address: "Kadıköy/İstanbul",
    gsmNumber: "+905350000000",
    email: "pro@test.dev",
  });
  const business = await getBusinessBySlug(SLUG);
  if (!business) throw new Error("seeded business not found");
  ali = { business, customerRef: ALI };
  veli = { business, customerRef: VELI };
});

beforeEach(async () => {
  await db.delete(bookings).where(eq(bookings.organizationId, ORG));
  await db
    .delete(bookingAttempts)
    .where(eq(bookingAttempts.organizationId, ORG));
});

afterAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db.$client.end();
});

const reload = (id: string) =>
  db.query.bookings.findFirst({ where: eq(bookings.id, id) });

async function bookAt(hour: number, who = ali) {
  const result = await createBookingTool(who, {
    service_id: haircut,
    starts_at: `${DAY}T${String(hour).padStart(2, "0")}:00:00Z`,
    customer_name: "Ali Yılmaz",
  });
  if (!result.ok) throw new Error(result.error.message);
  return result.data;
}

describe("create_booking", () => {
  it("books for the customer the assistant acts for, and reads the time back", async () => {
    const result = await createBookingTool(ali, {
      service_id: haircut,
      starts_at: `${DAY}T09:00:00Z`,
      customer_name: "  Ali Yılmaz  ",
    });

    if (!result.ok) throw new Error(result.error.message);
    expect(result.data).toMatchObject({
      status: "confirmed",
      checkout_url: null,
      hold_expires_at: null,
    });
    expect(result.data.local_label.endsWith("09:00")).toBe(true);
    const row = await reload(result.data.booking_id);
    expect(row).toMatchObject({
      customerName: "Ali Yılmaz",
      customerRef: ALI,
      source: "assistant",
    });
  });

  it("reads a time sent with its offset as the same instant", async () => {
    const result = await createBookingTool(ali, {
      service_id: haircut,
      starts_at: `${DAY}T13:00:00+03:00`,
      customer_name: "Ali Yılmaz",
    });

    if (!result.ok) throw new Error(result.error.message);
    expect((await reload(result.data.booking_id))?.startsAt.toISOString()).toBe(
      `${DAY}T10:00:00.000Z`
    );
  });

  it("refuses a phone that cannot be a number, and writes nothing", async () => {
    const result = await createBookingTool(ali, {
      service_id: haircut,
      starts_at: `${DAY}T11:00:00Z`,
      customer_name: "Ali Yılmaz",
      customer_phone: "ara beni",
    });

    expect(result).toMatchObject({ ok: false, error: { code: "invalid" } });
    expect(
      await db.query.bookings.findMany({
        where: eq(bookings.organizationId, ORG),
      })
    ).toHaveLength(0);
  });

  it("passes the core's refusals through by their code", async () => {
    expect(
      await createBookingTool(ali, {
        service_id: facial,
        starts_at: `${DAY}T12:00:00Z`,
        customer_name: "Ali Yılmaz",
      })
    ).toMatchObject({ ok: false, error: { code: "email_required" } });
    expect(
      await createBookingTool(ali, {
        service_id: retired,
        starts_at: `${DAY}T12:00:00Z`,
        customer_name: "Ali Yılmaz",
      })
    ).toMatchObject({ ok: false, error: { code: "not_found" } });
  });
});

describe("reschedule_booking", () => {
  it("moves the customer's booking, and says where it was", async () => {
    const booked = await bookAt(11);

    const result = await rescheduleBookingTool(ali, {
      booking_id: booked.booking_id,
      new_starts_at: `${DAY}T12:00:00Z`,
    });

    if (!result.ok) throw new Error(result.error.message);
    expect(result.data.local_label.endsWith("12:00")).toBe(true);
    expect(result.data.previous_local_label.endsWith("11:00")).toBe(true);
  });

  it("will not move someone else's booking", async () => {
    const booked = await bookAt(11);

    expect(
      await rescheduleBookingTool(veli, {
        booking_id: booked.booking_id,
        new_starts_at: `${DAY}T12:00:00Z`,
      })
    ).toMatchObject({ ok: false, error: { code: "not_found" } });
  });
});

describe("cancel_booking", () => {
  it("cancels the customer's booking", async () => {
    const booked = await bookAt(15);

    expect(
      await cancelBookingTool(ali, { booking_id: booked.booking_id })
    ).toEqual({
      ok: true,
      data: {
        cancelled: true,
        deposit_refunded: false,
        deposit_kept: false,
        refund_failed: false,
      },
    });
    expect((await reload(booked.booking_id))?.status).toBe("cancelled");
  });

  it("will not cancel someone else's booking", async () => {
    const booked = await bookAt(15);

    expect(
      await cancelBookingTool(veli, { booking_id: booked.booking_id })
    ).toMatchObject({ ok: false, error: { code: "not_found" } });
    expect((await reload(booked.booking_id))?.status).toBe("confirmed");
  });
});
```

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/assistant/write-tools.integration.test.ts`

Expected: FAIL. `./write-tools` cannot be resolved.

- [ ] **Step 2: Append the write half of the contract**

Append this to `lib/assistant/tool-definitions.ts`. It reuses that file's `bookingId` and `instant` atoms.

```ts
const createBookingInput = z.object({
  service_id: z.uuid(),
  starts_at: instant,
  customer_name: z.string().min(2).max(80),
  // Checked in the tool with the booking form's own phone rule.
  customer_phone: z.string().max(32).nullish(),
  customer_email: z.email().nullish(),
  expect_deposit_cents: z.number().int().positive().nullish(),
});

const rescheduleBookingInput = z.object({
  booking_id: bookingId,
  new_starts_at: instant,
});

const cancelBookingInput = z.object({
  booking_id: bookingId,
  expect_refund: z.boolean().optional(),
});

export const WRITE_TOOLS = {
  create_booking: {
    title: "Randevu oluştur",
    description:
      "Book a slot for this customer — only after they confirmed the exact service, time and kapora. With a kapora the slot is held for 30 minutes and checkout_url is the payment page to send them; customer_email is then required. Pass expect_deposit_cents as the kapora you showed them (null for none): if it changed since, nothing is booked (terms_changed).",
    input: createBookingInput,
    output: z.object({
      booking_id: z.uuid(),
      status: z.enum(["confirmed", "pending"]),
      starts_at: instant,
      local_label: z.string(),
      checkout_url: z.url().nullable(),
      hold_expires_at: instant.nullable(),
    }),
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
      openWorldHint: true,
    },
  },
  reschedule_booking: {
    title: "Randevuyu taşı",
    description:
      "Move one of this customer's confirmed bookings to another free start time (from find_slots), keeping its length. A booking still waiting for its kapora cannot be moved.",
    input: rescheduleBookingInput,
    output: z.object({
      booking_id: z.uuid(),
      starts_at: instant,
      local_label: z.string(),
      previous_local_label: z.string(),
    }),
    annotations: {
      readOnlyHint: false,
      destructiveHint: true,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
  cancel_booking: {
    title: "Randevuyu iptal et",
    description:
      "Cancel one of this customer's bookings. With at least 24 hours' notice a paid kapora is refunded; later, the business keeps it. Pass expect_refund as what you told them: if that is no longer true, nothing is cancelled (refund_window_closed).",
    input: cancelBookingInput,
    output: z.object({
      cancelled: z.literal(true),
      deposit_refunded: z.boolean(),
      deposit_kept: z.boolean(),
      refund_failed: z.boolean(),
    }),
    annotations: {
      readOnlyHint: false,
      destructiveHint: true,
      idempotentHint: true,
      openWorldHint: true,
    },
  },
};

/** Every tool /api/mcp serves, read and write. */
export const TOOL_DEFINITIONS = { ...READ_TOOLS, ...WRITE_TOOLS };

export type CreateBookingInput = z.infer<typeof createBookingInput>;
export type CreateBookingOutput = z.infer<
  typeof WRITE_TOOLS.create_booking.output
>;
export type RescheduleBookingInput = z.infer<typeof rescheduleBookingInput>;
export type RescheduleBookingOutput = z.infer<
  typeof WRITE_TOOLS.reschedule_booking.output
>;
export type CancelBookingInput = z.infer<typeof cancelBookingInput>;
export type CancelBookingOutput = z.infer<
  typeof WRITE_TOOLS.cancel_booking.output
>;
```

- [ ] **Step 3: Implement the write tools**

Create `lib/assistant/write-tools.ts`:

```ts
import "server-only";
import { revalidatePath } from "next/cache";
import { FALLBACK_IP } from "@/lib/booking/client-ip";
import { createBookingCore } from "@/lib/booking/create-booking-core";
import { customerCancelCore } from "@/lib/booking/customer-cancel-core";
import { customerRescheduleCore } from "@/lib/booking/customer-reschedule-core";
import { optionalPhoneSchema } from "@/lib/phone";
import { localLabel } from "./format";
import type { AssistantIdentity } from "./identity";
import type {
  CancelBookingInput,
  CancelBookingOutput,
  CreateBookingInput,
  CreateBookingOutput,
  RescheduleBookingInput,
  RescheduleBookingOutput,
} from "./tool-definitions";
import { type ToolResult, toolError } from "./tool-result";

/** The owner's panel lists bookings; a change made in chat must show there. */
function refreshPanel() {
  revalidatePath("/panel/randevular");
  revalidatePath("/panel");
}

export async function createBookingTool(
  { business, customerRef }: AssistantIdentity,
  args: CreateBookingInput
): Promise<ToolResult<CreateBookingOutput>> {
  const customerName = args.customer_name.trim();
  if (customerName.length < 2) {
    return toolError("invalid", "Müşteri adı en az 2 karakter olmalı");
  }
  // The booking form's own rule, so a chat booking stores a phone the same way.
  const phone = optionalPhoneSchema.safeParse(args.customer_phone ?? null);
  if (!phone.success) {
    return toolError(
      "invalid",
      phone.error.issues[0]?.message ?? "Geçerli bir telefon numarası girin"
    );
  }

  const result = await createBookingCore({
    business,
    serviceId: args.service_id,
    startsAt: new Date(args.starts_at),
    customerName,
    customerEmail: args.customer_email ?? null,
    customerPhone: phone.data,
    source: "assistant",
    customerRef,
    throttle: { customerRef },
    // The assistant calls from its own servers, never the customer's; iyzico
    // still wants a buyer IP, and this is the documented sandbox stand-in.
    customerIp: FALLBACK_IP,
    expectDepositCents: args.expect_deposit_cents,
  });
  if (!result.ok) return toolError(result.code, result.message);

  refreshPanel();
  const { booking } = result;
  return {
    ok: true,
    data: {
      booking_id: booking.id,
      status: booking.status === "pending" ? "pending" : "confirmed",
      starts_at: booking.startsAt.toISOString(),
      local_label: localLabel(booking.startsAt, business.profile.timezone),
      checkout_url: result.checkoutUrl ?? null,
      hold_expires_at: result.holdExpiresAt?.toISOString() ?? null,
    },
  };
}

export async function rescheduleBookingTool(
  { business, customerRef }: AssistantIdentity,
  args: RescheduleBookingInput
): Promise<ToolResult<RescheduleBookingOutput>> {
  const result = await customerRescheduleCore({
    business,
    bookingId: args.booking_id,
    customerRef,
    newStartsAt: new Date(args.new_starts_at),
  });
  if (!result.ok) return toolError(result.code, result.message);

  refreshPanel();
  const { timezone } = business.profile;
  return {
    ok: true,
    data: {
      booking_id: result.booking.id,
      starts_at: result.booking.startsAt.toISOString(),
      local_label: localLabel(result.booking.startsAt, timezone),
      previous_local_label: localLabel(result.previousStartsAt, timezone),
    },
  };
}

export async function cancelBookingTool(
  { business, customerRef }: AssistantIdentity,
  args: CancelBookingInput
): Promise<ToolResult<CancelBookingOutput>> {
  const result = await customerCancelCore({
    organizationId: business.organizationId,
    bookingId: args.booking_id,
    customerRef,
    expectRefund: args.expect_refund ?? false,
    customerIp: FALLBACK_IP,
  });
  if (!result.ok) return toolError(result.code, result.message);

  refreshPanel();
  return {
    ok: true,
    data: {
      cancelled: true,
      deposit_refunded: result.depositRefunded,
      deposit_kept: result.depositKept,
      refund_failed: result.refundFailed,
    },
  };
}
```

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/assistant/write-tools.integration.test.ts`

Expected: PASS (8 tests).

- [ ] **Step 5: Types, lint, commit**

```bash
bun run typecheck
bun run check:fix && bun run check
git add lib/assistant/tool-definitions.ts lib/assistant/write-tools.ts lib/assistant/write-tools.integration.test.ts
git commit -m "feat: the assistant's write tools, over the shared booking cores" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `/api/mcp` — registration, the route, and an end-to-end protocol test

**Files:**
- Create: `lib/assistant/mcp-server.ts`
- Create: `app/api/mcp/route.ts`
- Create: `lib/assistant/mcp-route.integration.test.ts`
- Modify: `README.md` (an engineering-notes section, a routes row, testing bullets)

**Interfaces:**
- Consumes: `TOOL_DEFINITIONS` (Task 9); the read tools (Task 8) and write tools (Task 9); `identityFrom` and `authenticateAssistant` (Task 7).
- Produces: `registerAssistantTools(server: McpServer): void`, and `POST(req: Request): Promise<Response>` at `/api/mcp`.

- [ ] **Step 1: Write the failing protocol test**

Create `lib/assistant/mcp-route.integration.test.ts`:

```ts
/**
 * /api/mcp end to end, through the real MCP client: the protocol, the door in
 * front of it, and the tools behind it, against the database. Only the
 * network is skipped — the client's fetch calls the route's POST directly.
 */
import { eq } from "drizzle-orm";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";

vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("next/headers", () => ({ headers: async () => new Map() }));
vi.mock("@/lib/email/booking-notifications", () => ({
  sendBookingConfirmedEmails: vi.fn(),
  sendBookingCancelledEmails: vi.fn(),
  sendBookingRescheduledEmail: vi.fn(),
}));

const TOKEN = "itest-mcp-token";
process.env.ASSISTANT_MCP_TOKEN = TOKEN;

const { POST } = await import("@/app/api/mcp/route");
const { Client, StreamableHTTPClientTransport } = await import(
  "@modelcontextprotocol/client"
);
const { db } = await import("@/lib/db");
const { organization } = await import("@/lib/db/schema/auth-schema");
const { availabilityRules } = await import(
  "@/lib/db/schema/availability-schema"
);
const { bookings } = await import("@/lib/db/schema/booking-schema");
const { businessProfiles } = await import("@/lib/db/schema/business-schema");
const { services } = await import("@/lib/db/schema/service-schema");

const ORG = "org_itest_mcp_route";
const SLUG = "itest-mcp-route";
const REF = `eval:${"a".repeat(26)}`;
const DAY = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
const URL_ = "http://localhost:3000/api/mcp";
let serviceId: string;
const clients: InstanceType<typeof Client>[] = [];

const assistant = {
  authorization: `Bearer ${TOKEN}`,
  "x-rezerve-business": SLUG,
  "x-rezerve-customer": REF,
};

async function connect(headers: Record<string, string> = assistant) {
  const transport = new StreamableHTTPClientTransport(new URL(URL_), {
    fetch: (url, init) => POST(new Request(url, init)),
    requestInit: { headers },
  });
  const client = new Client({ name: "itest", version: "0.0.0" });
  await client.connect(transport);
  clients.push(client);
  return client;
}

beforeAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db
    .insert(organization)
    .values({ id: ORG, name: ORG, slug: SLUG, createdAt: new Date() });
  await db.insert(businessProfiles).values({
    organizationId: ORG,
    timezone: "UTC",
    minLeadTimeMinutes: 0,
    assistantEnabled: true,
  });
  const [service] = await db
    .insert(services)
    .values({
      organizationId: ORG,
      name: "Saç Kesimi",
      durationMinutes: 45,
      priceCents: 80_000,
    })
    .returning({ id: services.id });
  serviceId = service.id;
  await db.insert(availabilityRules).values({
    organizationId: ORG,
    weekday: new Date(`${DAY}T12:00:00Z`).getUTCDay(),
    startMinutes: 9 * 60,
    endMinutes: 17 * 60,
  });
});

afterEach(async () => {
  await Promise.all(clients.splice(0).map((client) => client.close()));
});

afterAll(async () => {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db.$client.end();
});

describe("/api/mcp", () => {
  it("lists the eight tools, and marks which of them write", async () => {
    const client = await connect();

    const { tools } = await client.listTools();

    expect(tools.map((tool) => tool.name).sort()).toEqual([
      "cancel_booking",
      "create_booking",
      "find_slots",
      "get_booking",
      "get_business_info",
      "list_my_bookings",
      "list_services",
      "reschedule_booking",
    ]);
    expect(
      tools
        .filter((tool) => tool.annotations?.readOnlyHint === false)
        .map((tool) => tool.name)
        .sort()
    ).toEqual(["cancel_booking", "create_booking", "reschedule_booking"]);
  });

  it("finds a slot and books it for the customer named in the headers", async () => {
    const client = await connect();

    const found = await client.callTool({
      name: "find_slots",
      arguments: {
        service_id: serviceId,
        from_date: DAY,
        to_date: DAY,
        limit: 1,
      },
    });
    const [slot] = (found.structuredContent as { slots: { starts_at: string }[] })
      .slots;
    const booked = await client.callTool({
      name: "create_booking",
      arguments: {
        service_id: serviceId,
        starts_at: slot.starts_at,
        customer_name: "Elif Kaya",
      },
    });

    expect(booked.isError).not.toBe(true);
    const { booking_id } = booked.structuredContent as { booking_id: string };
    const row = await db.query.bookings.findFirst({
      where: eq(bookings.id, booking_id),
    });
    expect(row).toMatchObject({
      source: "assistant",
      customerRef: REF,
      status: "confirmed",
    });
  });

  it("answers a refused booking as a tool error carrying its code", async () => {
    const client = await connect();

    const result = await client.callTool({
      name: "create_booking",
      arguments: {
        service_id: serviceId,
        starts_at: `${DAY}T09:07:00Z`,
        customer_name: "Elif Kaya",
      },
    });

    expect(result.isError).toBe(true);
    expect(result.structuredContent).toMatchObject({ code: "slot_unavailable" });
  });

  it("turns a request without the token away before any tool runs", async () => {
    const response = await POST(
      new Request(URL_, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-rezerve-business": SLUG,
          "x-rezerve-customer": REF,
        },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
      })
    );

    expect(response.status).toBe(401);
  });
});
```

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/assistant/mcp-route.integration.test.ts`

Expected: FAIL. `@/app/api/mcp/route` cannot be resolved.

- [ ] **Step 2: Register the tools**

Create `lib/assistant/mcp-server.ts`:

```ts
import "server-only";
import type { CallToolResult, McpServer } from "@modelcontextprotocol/server";
import { identityFrom } from "./identity";
import {
  findSlots,
  getBooking,
  getBusinessInfo,
  listMyBookings,
  listServices,
} from "./read-tools";
import { TOOL_DEFINITIONS } from "./tool-definitions";
import type { ToolResult } from "./tool-result";
import {
  cancelBookingTool,
  createBookingTool,
  rescheduleBookingTool,
} from "./write-tools";

type Definition = (typeof TOOL_DEFINITIONS)[keyof typeof TOOL_DEFINITIONS];

/** A definition as registerTool takes it — specific types kept per tool. */
function config<D extends Definition>(definition: D) {
  return {
    title: definition.title,
    description: definition.description,
    inputSchema: definition.input as D["input"],
    outputSchema: definition.output as D["output"],
    annotations: definition.annotations,
  };
}

/**
 * A tool's result as MCP carries it: the structured object for the client to
 * read, the same as JSON text for clients that only show text, and refusals
 * as tool errors carrying their stable code — the model reads those and tries
 * something else, which is what a refusal is for. The SDK checks successes
 * against the output schema and leaves errors alone.
 */
function asCallToolResult<T extends Record<string, unknown>>(
  result: ToolResult<T>
): CallToolResult {
  const payload = result.ok ? result.data : result.error;
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    structuredContent: payload,
    ...(result.ok ? {} : { isError: true }),
  };
}

export function registerAssistantTools(server: McpServer): void {
  const tools = TOOL_DEFINITIONS;

  server.registerTool(
    "get_business_info",
    config(tools.get_business_info),
    async (_args, ctx) =>
      asCallToolResult(await getBusinessInfo(identityFrom(ctx.http?.authInfo)))
  );
  server.registerTool(
    "list_services",
    config(tools.list_services),
    async (_args, ctx) =>
      asCallToolResult(await listServices(identityFrom(ctx.http?.authInfo)))
  );
  server.registerTool(
    "find_slots",
    config(tools.find_slots),
    async (args, ctx) =>
      asCallToolResult(
        await findSlots(identityFrom(ctx.http?.authInfo), args)
      )
  );
  server.registerTool(
    "list_my_bookings",
    config(tools.list_my_bookings),
    async (_args, ctx) =>
      asCallToolResult(await listMyBookings(identityFrom(ctx.http?.authInfo)))
  );
  server.registerTool(
    "get_booking",
    config(tools.get_booking),
    async (args, ctx) =>
      asCallToolResult(
        await getBooking(identityFrom(ctx.http?.authInfo), args)
      )
  );
  server.registerTool(
    "create_booking",
    config(tools.create_booking),
    async (args, ctx) =>
      asCallToolResult(
        await createBookingTool(identityFrom(ctx.http?.authInfo), args)
      )
  );
  server.registerTool(
    "reschedule_booking",
    config(tools.reschedule_booking),
    async (args, ctx) =>
      asCallToolResult(
        await rescheduleBookingTool(identityFrom(ctx.http?.authInfo), args)
      )
  );
  server.registerTool(
    "cancel_booking",
    config(tools.cancel_booking),
    async (args, ctx) =>
      asCallToolResult(
        await cancelBookingTool(identityFrom(ctx.http?.authInfo), args)
      )
  );
}
```

- [ ] **Step 3: The route**

Create `app/api/mcp/route.ts`:

```ts
import { createMcpHandler } from "mcp-handler";
import { registerAssistantTools } from "@/lib/assistant/mcp-server";
import { authenticateAssistant } from "@/lib/assistant/request-auth";

/**
 * The chat assistant's way into Rezerve: a Model Context Protocol endpoint
 * (Streamable HTTP, stateless), used by rezerve-asistan.
 *
 * Every request passes the door first — token, business, customer
 * (lib/assistant/request-auth.ts) — and the identity it settles rides along
 * on `req.auth`, where mcp-handler hands it to each tool. POST only: the
 * stateless protocol has no use for GET, so Next answers it with 405.
 */
const mcp = createMcpHandler(registerAssistantTools, {
  serverInfo: { name: "rezerve", version: "1.0.0" },
});

export async function POST(req: Request): Promise<Response> {
  const auth = await authenticateAssistant(
    req,
    process.env.ASSISTANT_MCP_TOKEN
  );
  if (!auth.ok) return auth.response;
  req.auth = auth.authInfo;
  return mcp(req);
}
```

- [ ] **Step 4: Run the protocol test**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/assistant/mcp-route.integration.test.ts`

Expected: PASS (4 tests).

If `typecheck` or the test reports that `config(...)`'s `annotations` (a readonly `as const` object) is not assignable to `ToolAnnotations`, change `READ_ONLY` in `tool-definitions.ts` to a plain object, without `as const`, typed as `{ readOnlyHint: boolean; openWorldHint: boolean }`.

- [ ] **Step 5: The production build serves the route**

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run build`

Expected: the build succeeds, and the route list shows `ƒ /api/mcp`.

If bundling fails inside `@modelcontextprotocol/server` or `mcp-handler`, add both to `serverExternalPackages` in `next.config.ts` and run the build again:

```ts
serverExternalPackages: ["iyzipay", "@modelcontextprotocol/server", "mcp-handler"],
```

- [ ] **Step 6: Document the endpoint in the README**

In `README.md`, inside the `<details>` engineering notes and directly after the `### Architecture notes` section, add:

````markdown
### The assistant's endpoint: /api/mcp

Rezerve Asistan, a chat receptionist (in progress), books through a Model
Context Protocol endpoint rather than the database. It runs the same booking
rules the booking page does — `lib/booking/create-booking-core.ts`,
`customer-cancel-core.ts` and `customer-reschedule-core.ts`, which the
page's server actions now wrap — so the exclusion constraint, the throttle,
the entitlements and the kapora rules hold for a chat customer exactly as
for a visitor.

- **Who is asking travels in headers, never in arguments.** `Authorization:
  Bearer <ASSISTANT_MCP_TOKEN>` (fail-closed, constant-time — the same check
  as the cron's), `X-Rezerve-Business: <slug>` and `X-Rezerve-Customer:
  <customer ref>`. No tool takes a business or a customer, so the model
  cannot name someone else's.
- **A chat customer is a customer ref** — an HMAC the assistant derives from
  the channel's user id, so no Telegram id or phone number reaches this
  database. It is the credential for moving or cancelling, the way a
  booking's uuid is on its page.
- **The customer agreed to specific terms.** `create_booking` takes the
  kapora the customer was shown and `cancel_booking` what they were told
  about the refund; if either changed since, nothing is written
  (`terms_changed`, `refund_window_closed`).
- **Off until switched on:** a business answers 403 unless
  `assistant_enabled`. The demos have it on.

| Tool                 | Writes | What it does                                        |
| -------------------- | ------ | --------------------------------------------------- |
| `get_business_info`  | —      | Contact details, notes, today's date in the business timezone |
| `list_services`      | —      | Active services, with the kapora actually collected |
| `find_slots`         | —      | Free starts across up to 14 days                     |
| `list_my_bookings`   | —      | The customer's upcoming bookings and live holds      |
| `get_booking`        | —      | One of their bookings, e.g. whether it is paid       |
| `create_booking`     | ✓      | Book, or hold for 30 minutes with a payment page     |
| `reschedule_booking` | ✓      | Move to another free start, keeping the length       |
| `cancel_booking`     | ✓      | Cancel under the 24-hour kapora rule                 |
````

In the `### Routes` table, add this row after the `/api/r/[slug]/slots` row:

```markdown
| `/api/mcp`                                                | The chat assistant's MCP endpoint (bearer token)     |
```

In the `### Testing` list, add these bullets before the `e2e/` bullet:

```markdown
- `lib/booking/create-booking-core.integration.test.ts`,
  `customer-cancel-core.integration.test.ts`,
  `customer-reschedule-core.integration.test.ts` — the shared booking rules
  as the assistant calls them: no email without a kapora, a kapora that
  changed since the customer agreed, another chat customer's booking refused
  even with its real id, and two moves into one slot of which exactly one wins
- `lib/assistant/request-auth.integration.test.ts` — the endpoint's door:
  fail-closed token, a customer ref taken exactly or not at all, an unknown
  business and a switched-off one answered alike
- `lib/assistant/mcp-route.integration.test.ts` — the endpoint through the
  real MCP client: tools listed, a slot found and booked, a refusal returned
  as a tool error with its code
```

- [ ] **Step 7: Whole suite, types, lint, commit**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test
bun run typecheck
bun run check:fix && bun run check
git add lib/assistant/mcp-server.ts app/api/mcp/route.ts lib/assistant/mcp-route.integration.test.ts README.md
git commit -m "feat: serve the assistant's tools over MCP at /api/mcp" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

If Step 5 changed `next.config.ts`, add it to `git add` as well.

---

### Task 11: `bun run mcp:schema` — the contract as JSON Schema

**Files:**
- Create: `lib/assistant/tool-schema.ts`
- Create: `lib/assistant/tool-schema.test.ts`
- Create: `scripts/export-mcp-schema.ts`
- Modify: `package.json` (`mcp:schema` script)
- Modify: `README.md` (one sentence in the `/api/mcp` section that Task 10 added)

**Interfaces:**
- Consumes: `TOOL_DEFINITIONS` (Task 9).
- Produces: `toolSchemaDocument(): { server: "rezerve"; tools: Array<{ name; title; description; annotations; inputSchema; outputSchema }> }`. The `inputSchema` and `outputSchema` fields are JSON Schema objects. `bun run mcp:schema` prints this document.

- [ ] **Step 1: Write the failing test**

Create `lib/assistant/tool-schema.test.ts`:

```ts
/**
 * The tool contract as the assistant's repository receives it. The one rule
 * that matters most is structural: no tool lets the model name a business or
 * a customer — those come from headers only.
 */
import { describe, expect, it } from "vitest";
import { toolSchemaDocument } from "./tool-schema";

const document = toolSchemaDocument();
type JsonSchema = { properties?: Record<string, { format?: string }>; required?: string[] };
const input = (name: string) =>
  document.tools.find((tool) => tool.name === name)?.inputSchema as JsonSchema;

describe("toolSchemaDocument", () => {
  it("publishes the eight tools", () => {
    expect(document.tools.map((tool) => tool.name).sort()).toEqual([
      "cancel_booking",
      "create_booking",
      "find_slots",
      "get_booking",
      "get_business_info",
      "list_my_bookings",
      "list_services",
      "reschedule_booking",
    ]);
  });

  it("never takes a business or a customer as an argument", () => {
    for (const tool of document.tools) {
      for (const property of Object.keys(
        (tool.inputSchema as JsonSchema).properties ?? {}
      )) {
        expect(property).not.toMatch(
          /slug|business|organization|customer_ref|customer_id/
        );
      }
    }
  });

  it("requires what a booking cannot be made without, and nothing more", () => {
    expect(input("create_booking").required?.sort()).toEqual([
      "customer_name",
      "service_id",
      "starts_at",
    ]);
  });

  it("describes instants as date-times", () => {
    expect(input("create_booking").properties?.starts_at.format).toBe(
      "date-time"
    );
  });
});
```

Run: `bun run test lib/assistant/tool-schema.test.ts`

Expected: FAIL. `./tool-schema` cannot be resolved.

- [ ] **Step 2: Implement**

Create `lib/assistant/tool-schema.ts`:

```ts
import { z } from "zod";
import { TOOL_DEFINITIONS } from "./tool-definitions";

/**
 * The assistant's tools as JSON Schema — the contract rezerve-asistan keeps a
 * copy of and tests itself against, so the two repositories cannot drift
 * apart unnoticed. Pure: no database, no request.
 */
export function toolSchemaDocument() {
  return {
    server: "rezerve" as const,
    tools: Object.entries(TOOL_DEFINITIONS).map(([name, definition]) => ({
      name,
      title: definition.title,
      description: definition.description,
      annotations: definition.annotations,
      inputSchema: z.toJSONSchema(definition.input),
      outputSchema: z.toJSONSchema(definition.output),
    })),
  };
}
```

Create `scripts/export-mcp-schema.ts`:

```ts
/**
 * Print the assistant tools' JSON Schemas — the contract the rezerve-asistan
 * repository keeps a copy of, and fails its tests against when they drift.
 *
 *   bun run mcp:schema > ../rezerve-asistan/contract/rezerve-mcp-tools.json
 */
import { toolSchemaDocument } from "@/lib/assistant/tool-schema";

console.log(JSON.stringify(toolSchemaDocument(), null, 2));
```

In `package.json` `scripts`, add after `"seed:demo"`:

```json
    "mcp:schema": "bun run scripts/export-mcp-schema.ts",
```

- [ ] **Step 3: Run the test and the script**

```bash
bun run test lib/assistant/tool-schema.test.ts
bun run mcp:schema | head -20
```

Expected: PASS (4 tests). The script prints JSON that starts with `"server": "rezerve"` followed by the first tool.

- [ ] **Step 4: Point the README at the test that keeps the rule**

In `README.md`'s `/api/mcp` section (added in Task 10), replace

```markdown
  cannot name someone else's.
```

with

```markdown
  cannot name someone else's; `lib/assistant/tool-schema.test.ts` keeps it so.
```

- [ ] **Step 5: Types, lint, commit**

```bash
bun run typecheck
bun run check:fix && bun run check
git add lib/assistant/tool-schema.ts lib/assistant/tool-schema.test.ts scripts/export-mcp-schema.ts package.json README.md
git commit -m "feat: print the assistant's tool contract as JSON Schema" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The demos switch the assistant on, and the eval business exists

**Files:**
- Modify: `lib/demo/credentials.ts` (`DEMO_EVAL`, deliberately not in `DEMO_ACCOUNTS`)
- Modify: `lib/demo/reset.ts` (`assistantEnabled` on, an eval page entry, `resetBusiness` options, `resetEvalBusiness`)
- Create: `scripts/seed-eval.ts`
- Modify: `package.json` (`seed:eval` script)
- Create: `lib/demo/reset-eval.integration.test.ts`
- Modify: `lib/demo/reset.integration.test.ts` (one assertion in `expectPublished`)
- Modify: `README.md` (the Development scripts line)

**Interfaces:**
- Consumes: `businessProfiles.assistantEnabled` (Task 1).
- Produces:
  ```ts
  export const DEMO_EVAL: DemoAccount; // orgId "org_rezerve_eval", slug "eval"
  export function resetEvalBusiness(options?: { now?: Date; subMerchantKey?: string | null }): Promise<DemoResetSummary>;
  ```
  It also produces `bun run seed:eval` (local databases only).

- [ ] **Step 1: Write the failing tests**

Create `lib/demo/reset-eval.integration.test.ts`:

```ts
/**
 * The eval harness's business: the Pro demo's services and hours with no
 * booking history, so a scripted conversation knows which times are free —
 * and one run puts it back after a scenario has used it.
 */
import { eq } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const { resetEvalBusiness } = await import("./reset");
const { DEMO_ACCOUNTS, DEMO_EVAL } = await import("./credentials");
const { db } = await import("@/lib/db");
const { organization, user } = await import("@/lib/db/schema/auth-schema");
const { availabilityRules } = await import(
  "@/lib/db/schema/availability-schema"
);
const { orgPayoutAccounts, orgSubscriptions } = await import(
  "@/lib/db/schema/billing-schema"
);
const { bookings } = await import("@/lib/db/schema/booking-schema");
const { businessProfiles } = await import("@/lib/db/schema/business-schema");
const { services } = await import("@/lib/db/schema/service-schema");

const ORG = DEMO_EVAL.orgId;
const KEY = "itest-eval-submerchant-key";

async function cleanUp() {
  await db.delete(organization).where(eq(organization.id, ORG));
  await db.delete(user).where(eq(user.email, DEMO_EVAL.email));
}

beforeAll(cleanUp);
afterAll(async () => {
  await cleanUp();
  await db.$client.end();
});

describe("resetEvalBusiness", () => {
  it("seeds the Pro demo's setup, switched on for the assistant, with no bookings", async () => {
    expect(await resetEvalBusiness({ subMerchantKey: KEY })).toEqual({
      bookings: 0,
      slugRestored: true,
      collectsKapora: true,
    });

    const [org, profile, svc, rules, rows, sub, payout] = await Promise.all([
      db.query.organization.findFirst({ where: eq(organization.id, ORG) }),
      db.query.businessProfiles.findFirst({
        where: eq(businessProfiles.organizationId, ORG),
      }),
      db.query.services.findMany({ where: eq(services.organizationId, ORG) }),
      db.query.availabilityRules.findMany({
        where: eq(availabilityRules.organizationId, ORG),
      }),
      db.query.bookings.findMany({ where: eq(bookings.organizationId, ORG) }),
      db.query.orgSubscriptions.findFirst({
        where: eq(orgSubscriptions.organizationId, ORG),
      }),
      db.query.orgPayoutAccounts.findFirst({
        where: eq(orgPayoutAccounts.organizationId, ORG),
      }),
    ]);
    expect(org?.slug).toBe("eval");
    expect(profile?.assistantEnabled).toBe(true);
    expect(profile?.timezone).toBe("Europe/Istanbul");
    expect(svc.map((s) => s.name).sort()).toEqual([
      "Cilt Bakımı",
      "Manikür",
      "Saç Kesimi",
    ]);
    expect(rules).toHaveLength(12);
    expect(rows).toHaveLength(0);
    expect(sub).toMatchObject({ plan: "pro", status: "active" });
    expect(payout?.subMerchantKey).toBe(KEY);
  });

  it("clears whatever a scenario left behind", async () => {
    const [service] = await db.query.services.findMany({
      where: eq(services.organizationId, ORG),
    });
    const startsAt = new Date(Date.now() + 86_400_000);
    await db.insert(bookings).values({
      organizationId: ORG,
      serviceId: service.id,
      customerName: "Senaryo Müşterisi",
      startsAt,
      endsAt: new Date(startsAt.getTime() + service.durationMinutes * 60_000),
      source: "assistant",
      customerRef: `eval:${"a".repeat(26)}`,
    });

    await resetEvalBusiness({ subMerchantKey: KEY });

    expect(
      await db.query.bookings.findMany({ where: eq(bookings.organizationId, ORG) })
    ).toHaveLength(0);
  });

  it("is never published, so the nightly reset leaves it alone", () => {
    expect(DEMO_ACCOUNTS.map((demo) => demo.orgId)).not.toContain(ORG);
  });
});
```

In `lib/demo/reset.integration.test.ts`, inside `expectPublished`, add after the `slotGranularityMinutes` assertion:

```ts
  expect(state.profile?.assistantEnabled).toBe(true);
```

Run: `DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/demo/reset-eval.integration.test.ts lib/demo/reset.integration.test.ts`

Expected: FAIL. `DEMO_EVAL` and `resetEvalBusiness` do not exist, and `assistantEnabled` is false on the demos.

- [ ] **Step 2: The eval account**

In `lib/demo/credentials.ts`, after `DEMO_FREE` and before `DEMO_ACCOUNTS`, add:

```ts
/**
 * The rezerve-asistan eval harness's business: the Pro demo's plan, services
 * and hours, but no booking history, so a scripted conversation knows which
 * times are free. Seeded only into local databases (`bun run seed:eval`) —
 * deliberately not in DEMO_ACCOUNTS, so it is never published and the
 * nightly reset never touches it.
 */
export const DEMO_EVAL: DemoAccount = {
  orgId: "org_rezerve_eval",
  slug: "eval",
  name: "Rezerve Eval Salon",
  ownerName: "Eval Salon",
  email: "eval@rezerve.app",
  password: DEMO_PASSWORD,
};
```

- [ ] **Step 3: Teach the reset about it**

In `lib/demo/reset.ts`:

1. Change the credentials import to `import { DEMO_ACCOUNTS, DEMO_EVAL, DEMO_FREE, DEMO_PRO, type DemoAccount } from "./credentials";`.

2. In `DEMO_PROFILE`, add after `contactEmail: null,`:

```ts
  // The demos show the chat assistant; a real business switches it on itself.
  assistantEnabled: true,
```

3. In `DEMO_PAGE`, add a third entry after the `DEMO_FREE.slug` one:

```ts
  [DEMO_EVAL.slug]: {
    phone: "0212 000 00 01",
    address: "Örnek Sk. No:3, Kadıköy / İstanbul",
    description:
      "rezerve-asistan değerlendirmelerinin örnek işletmesi — burada alınan randevular gerçek değildir.",
  },
```

4. Replace `resetDemo` and the `resetBusiness` signature, and add `resetEvalBusiness`, so that this part of the file reads:

```ts
export async function resetDemo({
  now = new Date(),
  subMerchantKey = process.env.DEMO_SUBMERCHANT_KEY,
}: {
  now?: Date;
  /** Defaults to DEMO_SUBMERCHANT_KEY; null means none, whatever the env says. */
  subMerchantKey?: string | null;
} = {}): Promise<Record<string, DemoResetSummary>> {
  const summary: Record<string, DemoResetSummary> = {};
  for (const demo of DEMO_ACCOUNTS) {
    summary[demo.slug] = await resetBusiness(demo, now, subMerchantKey, {
      pro: demo.orgId === DEMO_PRO.orgId,
      withHistory: true,
    });
  }
  return summary;
}

/**
 * The rezerve-asistan eval business: the Pro demo's setup with no history.
 * Not part of the nightly reset — see DEMO_EVAL.
 */
export function resetEvalBusiness({
  now = new Date(),
  subMerchantKey = process.env.DEMO_SUBMERCHANT_KEY,
}: {
  now?: Date;
  subMerchantKey?: string | null;
} = {}): Promise<DemoResetSummary> {
  return resetBusiness(DEMO_EVAL, now, subMerchantKey, {
    pro: true,
    withHistory: false,
  });
}

type ResetOptions = {
  /** Restore Pro billing (kapora collected); otherwise the free plan. */
  pro: boolean;
  /** Seed the lived-in booking history the public demos show. */
  withHistory: boolean;
};

async function resetBusiness(
  demo: DemoAccount,
  now: Date,
  subMerchantKey: string | null | undefined,
  { pro, withHistory }: ResetOptions
): Promise<DemoResetSummary> {
```

5. Inside `resetBusiness`'s transaction, replace the history block and the Pro branch, i.e. from `const history = demoHistory({` up to and including the `if (demo.orgId === DEMO_PRO.orgId) { … }` block, with:

```ts
    const history = withHistory
      ? demoHistory({
          now,
          timezone: DEMO_PROFILE.timezone,
          hours: DEMO_HOURS,
          services: created,
        })
      : [];
    if (history.length > 0) {
      await tx
        .insert(bookings)
        .values(history.map((b) => ({ ...b, organizationId: orgId })));
    }

    if (pro) {
      const collectsKapora = await restoreProBilling(
        tx,
        demo,
        now,
        subMerchantKey
      );
      return { bookings: history.length, slugRestored, collectsKapora };
    }
```

6. Change `restoreProBilling`'s signature to `async function restoreProBilling(tx: Tx, demo: DemoAccount, now: Date, subMerchantKey: string | null | undefined): Promise<boolean>`. Inside it:
   - add `const orgId = demo.orgId;` as its first line;
   - replace `name: DEMO_PRO.name,` with `name: demo.name,`;
   - replace `email: DEMO_PRO.email,` with `email: demo.email,`.

   Its doc comment stays.

7. In the file's top doc comment, after the sentence ending "every write is scoped to their fixed ids.", add:

```ts
 * The same machinery also resets the eval harness's business
 * (resetEvalBusiness) — never from the cron.
```

- [ ] **Step 4: The seed script**

Create `scripts/seed-eval.ts`:

```ts
/**
 * Create the rezerve-asistan eval business, or put it back exactly as seeded:
 * /r/eval, with the Pro demo's services and hours and no bookings.
 *
 * Local databases only — it refuses a DATABASE_URL that is not on this
 * machine, the same rule the end-to-end suite keeps (playwright.config.ts).
 *
 *   DATABASE_URL=postgresql://<user>@localhost:5432/<db> bun run seed:eval
 */
import "dotenv/config";
import { db } from "@/lib/db";
import { DEMO_EVAL } from "@/lib/demo/credentials";
import { resetEvalBusiness } from "@/lib/demo/reset";

const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"];

async function main() {
  const url = process.env.DATABASE_URL;
  const host = url ? new URL(url).hostname : "";
  if (!LOCAL_HOSTS.includes(host)) {
    console.error(
      `Refusing to seed the eval business on "${host || "no database"}": local databases only.`
    );
    process.exit(1);
  }

  const summary = await resetEvalBusiness();
  console.log(`✓ ${DEMO_EVAL.slug}: reset — no bookings, assistant on`);
  if (summary.collectsKapora === false) {
    console.warn(
      "! No submerchant: kapora scenarios will fail. Put DEMO_SUBMERCHANT_KEY in .env and re-run."
    );
  }
  await db.$client.end();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
```

In `package.json` `scripts`, add after `"seed:demo"`:

```json
    "seed:eval": "bun run scripts/seed-eval.ts",
```

- [ ] **Step 5: Run the tests and the script**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test lib/demo/reset-eval.integration.test.ts lib/demo/reset.integration.test.ts
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run seed:eval
bun run seed:eval
```

Expected:
- **The tests:** PASS (3 new tests, and every existing reset test).
- **The first `seed:eval`**, on the local URL, prints `✓ eval: reset — no bookings, assistant on`.
- **The second `seed:eval`**, without the prefix, so it reads the Neon URL from `.env`, exits 1 with `Refusing to seed the eval business on "<neon host>"` and writes nothing.

- [ ] **Step 6: Document the script**

In `README.md`, in the `### Development` paragraph that lists "Useful scripts", add this after the `bun run seed:demo` sentence:

```markdown
`bun run seed:eval` creates or resets `/r/eval` — the Pro demo's services
and hours with no bookings, for the rezerve-asistan eval harness; it refuses
any `DATABASE_URL` not on this machine. `bun run mcp:schema` prints the
assistant's tools as JSON Schema.
```

- [ ] **Step 7: Whole suite, types, lint, commit**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test
bun run typecheck
bun run check:fix && bun run check
git add lib/demo/credentials.ts lib/demo/reset.ts lib/demo/reset-eval.integration.test.ts lib/demo/reset.integration.test.ts scripts/seed-eval.ts package.json README.md
git commit -m "feat: switch the assistant on for the demos, and seed an eval business" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Whole-branch verification

**Files:** none are created. This task only verifies; fix anything it finds, in the task that owns the code.

- [ ] **Step 1: The same gates CI runs, against the scratch database**

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run typecheck
bun run check
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run test
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test bun run build
bun run check:trace
```

Expected: all green. `check:trace` still finds the iyzico SDK's files in the traced output.

- [ ] **Step 2: End to end, on the e2e database**

`playwright.config.ts` refuses any database that is not local.

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_e2e bun run db:migrate
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_e2e bun run seed:demo
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_e2e bun run e2e
```

Expected: every Playwright test passes. The booking page, which now runs through `createBookingCore`, books exactly as before.

- [ ] **Step 3: The endpoint by hand, with MCP Inspector**

Run the dev server and the inspector in two terminals:

```bash
DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test ASSISTANT_MCP_TOKEN=local-token bun dev
npx @modelcontextprotocol/inspector
```

In the inspector:
1. Connect via Streamable HTTP to `http://localhost:3000/api/mcp`, with the headers:
   - `Authorization: Bearer local-token`;
   - `X-Rezerve-Business: eval` (seeded in Task 12);
   - `X-Rezerve-Customer: eval:aaaaaaaaaaaaaaaaaaaaaaaaaa`.
2. List the tools and run `list_services`, then `find_slots` for tomorrow, then `create_booking` on one of the returned `starts_at`.
3. Sign in to the panel as `eval@rezerve.app` / `rezerve-demo` and see the booking under `/panel/randevular`.
4. Change the token and see the inspector's connection refused.

- [ ] **Step 4: Report**

The branch `feat/assistant-mcp` holds Tasks 1–12. Do not push. List for the user what remains outside this plan:
- apply migration 0014 to Neon (`bun run db:migrate` with the Neon `DATABASE_URL`) before deploying;
- set `ASSISTANT_MCP_TOKEN` on Vercel;
- the next nightly demo reset switches the assistant on for `/r/demo` and `/r/demo-ucretsiz`.
