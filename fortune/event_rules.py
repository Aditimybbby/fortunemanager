"""Event policy and deterministic reward calculation (no Discord I/O)."""
import calendar
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

RULES = '''Event Rules

1. Invite Rules
1. Only EVEN-numbered invites are counted.
2. Vanity invites are NOT counted.
3. Invite check will be done by Falcon. Invalid invite = −1 invite.
4. Invites left after the event = BAN.
5. Checking invites in chat = RESET.
6. No count after creating a ticket.

2. Promo Rules
1. Server Promo = -4 invites
2. Server Promo and DM Promo follow the same rules.
3. Leave & Rejoin = −2 (not counted in Server Promo).
4. Mass Offline = No Reward.
5. No PFP / No Bio = RESET (−2 in Server Promo).
6. No Onboarding = −3 from real invites (−1 in Server Promo).
7. Account under 4 months = −3 from real invites (−1 in Server Promo).
8. No Status + Bio = DQ.

3. Reset / DQ Rules
1. J4J / J2J / J6J / ALT / TOKEN / MASS OFF = RESET.
2. Asking for payout = RESET.
3. Pinging staff in ticket = RESET.
4. Pinging & deleting messages = RESET.
5. No SS proof within 5 days = RESET.
6. Wrong ticket = RESET.
7. Disrespecting staff = RESET.
8. Calling us scam/scammer = RESET + DQ + BAN.

4. Rewards
1. No Limit = No Reward.
2. Payout = INSTANT.

5. Important
1. Rules must be followed strictly.
2. We have the right to change/update the rules at any time.
3. Invite Check Channel: Cmds

NOTE — FOLLOW ALL RULES STRICTLY'''

# Findings requiring evidence are explicitly recorded by staff, never guessed.
MANUAL_RULES = {
    'invalid': ('deduct', 1, 1),
    'no_bio': ('profile', 0, 2),
    'no_status_bio': ('dq', 0, 0),
    'mass_offline': ('reject', 0, 0),
    'j4j': ('reset', 0, 0), 'j2j': ('reset', 0, 0), 'j6j': ('reset', 0, 0),
    'alt': ('reset', 0, 0), 'token': ('reset', 0, 0), 'mass_off': ('reset', 0, 0),
    'asking_payout': ('reset', 0, 0), 'ping_staff': ('reset', 0, 0),
    'ping_delete': ('reset', 0, 0), 'wrong_ticket': ('reset', 0, 0),
    'disrespect': ('reset', 0, 0), 'scam_accusation': ('ban_review', 0, 0),
    'left_after_event': ('ban_review', 0, 0), 'no_limit': ('reject', 0, 0),
    'chat_check': ('reset', 0, 0), 'no_proof': ('reset', 0, 0),
}


def parse_rewards(text):
    result = []
    for line in text.strip().splitlines():
        match = re.fullmatch(r'\s*(\d+)\s*(?:invites?)?\s*=\s*(.+?)\s*', line, re.I)
        if not match:
            raise ValueError('Use one reward per line: 1 invite = $0.30 or 10 invites = Nitro Booster')
        threshold, label = int(match[1]), match[2]
        if not 1 <= threshold <= 1_000_000 or len(label) > 100:
            raise ValueError('Threshold must be 1–1,000,000; reward text must be at most 100 characters.')
        if threshold in [r['threshold'] for r in result]:
            raise ValueError('Each invite threshold must be unique.')
        money = re.fullmatch(r'(?:\$(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\$)', label)
        amount = str(Decimal(money[1] or money[2])) if money else None
        if ('$' in label and not money) or (amount is not None and not Decimal('0') < Decimal(amount) <= Decimal('1000000')):
            raise ValueError('Cash rewards must be $0.01–$1,000,000 with at most two decimal places.')
        result.append({'threshold': threshold, 'label': label, 'amount': amount})
    if not result or len(result) > 20:
        raise ValueError('Enter between 1 and 20 reward tiers.')
    return sorted(result, key=lambda r: r['threshold'])


def reward_for(count, rewards, selected_threshold=None):
    if selected_threshold is None:
        return ('Choose a reward' if any(count >= r['threshold'] for r in rewards) else 'No reward'), None
    tier = next((r for r in rewards if r['threshold'] == selected_threshold and count >= r['threshold']), None)
    if not tier:
        return 'No reward', None
    if tier['amount'] is not None:
        # A one-invite tier is a per-invite rate. Other tiers are fixed rewards.
        amount = Decimal(tier['amount']) * (count if tier['threshold'] == 1 else 1)
        return f'${amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)}', tier['threshold']
    return tier['label'], tier['threshold']


def four_months_before(value):
    month_index = value.year * 12 + value.month - 1 - 4
    year, month = divmod(month_index, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def calculate(rows, rewards, *, rules, promo, at, findings=(), proof_overdue=False, selected_threshold=None):
    raw = len(rows)
    deductions, blocked = [], []
    if rules:
        if promo == 'server':
            deductions.append(('Server promotion', 4))
        for row in rows:
            uid = row['member_id']
            if row.get('left'):
                deductions.append((f'{uid}: left', 2))
            if promo == 'dm' and row.get('rejoined'):
                deductions.append((f'{uid}: rejoined', 2))
            if row.get('no_avatar'):
                if promo == 'server':
                    deductions.append((f'{uid}: no profile picture', 2))
                else:
                    blocked.append(f'RESET: {uid} has no profile picture')
            if row.get('onboarding_missing'):
                deductions.append((f'{uid}: onboarding incomplete', 1 if promo == 'server' else 3))
            if datetime.fromisoformat(row['account_created_at']) > four_months_before(at):
                deductions.append((f'{uid}: account under four months', 1 if promo == 'server' else 3))
        for finding in findings:
            action, dm, server = MANUAL_RULES[finding['code']]
            if action == 'deduct' or (action == 'profile' and promo == 'server'):
                deductions.append((finding['code'], (server if promo == 'server' else dm) * finding['quantity']))
            else:
                blocked.append(f'{"reset" if action == "profile" else action}: {finding["code"]}')
        if proof_overdue:
            blocked.append('RESET: no screenshot proof within five days')
    net = max(0, raw - sum(n for _, n in deductions))
    eligible = net - net % 2 if rules else net
    reward, tier = reward_for(eligible, rewards, selected_threshold) if not blocked else ('No reward', None)
    return dict(raw=raw, deductions=deductions, net=net, eligible=eligible,
                rounded=net-eligible, blocked=blocked, reward=reward, tier=tier)
