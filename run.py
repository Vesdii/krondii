from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import discord
from discord.ext import commands, tasks
import sqlite3
import pytz
import re
#import subprocess

bot = commands.Bot('$')
con = sqlite3.connect('krondii.db')
dt_fmt = '%Y-%m-%d %H:%M'
newline_str = '%%%%NEWLINE%%%%'


############
# REMINDER #
############

@tasks.loop(minutes=1)
async def check():
    cur = con.cursor()
    now = datetime.now().strftime(dt_fmt)
    cur.execute('SELECT user,channel,message,rowid FROM reminders WHERE datetime = ?', (now,))
    reminders = cur.fetchall()
    sent = 0

    for user, channel, message, rowid in reminders:
        # Decide if destination is a DM or guild channel
        target = bot.get_user(user) if not channel else bot.get_channel(channel)

        # Send reminder and decrement count
        cur.execute('DELETE FROM reminders WHERE rowid = ?', (rowid,))
        cur.execute('UPDATE users SET reminder_count = reminder_count - 1 WHERE id = ?', (user,))
        message = message.replace(newline_str, '\n')
        await target.send(message)
        sent += 1
        con.commit()

    if sent > 0:
        log(f'{sent} reminders issued')

@check.before_loop
async def check_delay():
    # Flush old reminders
    cur = con.cursor()
    cur2 = con.cursor()
    now = datetime.now().strftime(dt_fmt)
    now = datetime.strptime(now, dt_fmt)
    for when, user, rowid in cur.execute('SELECT datetime,user,rowid FROM reminders'):
        when = datetime.strptime(when, dt_fmt)
        if when < now:
            cur2.execute('DELETE FROM reminders WHERE rowid = ?', (rowid,))
            cur2.execute('UPDATE users SET reminder_count = reminder_count - 1 WHERE id = ?', (user,))
    con.commit()

    # Wait until new minute
    now = datetime.now()
    when = now.strftime(dt_fmt)
    when = datetime.strptime(when, dt_fmt)
    when = when + relativedelta(minutes=+1)
    await discord.utils.sleep_until(when)
    log('Initialized')

@bot.command(aliases=['r'])
async def remind(ctx, when, *, message):
    await setreminder(ctx, when, message, False)

@bot.command(aliases=['rh'])
async def remindhere(ctx, when, *, message):
    await setreminder(ctx, when, message, True)

async def setreminder(ctx, when, message, here):
    cur = con.cursor()
    user = ctx.author.id
    now = datetime.now()
    cur.execute('SELECT reminder_count,timezone FROM users WHERE id = ?', (user,))
    result = cur.fetchone()
    reminder_count, timezone = 0, None
    if result:
        reminder_count, timezone = result
    else: # Create user entry if it doesn't exist
        create_user(user)

    # Reject reminder overture or increment count
    if reminder_count == 5:
        await ctx.send('You may only have up to 5 reminders registered.', delete_after=5)
        return
    cur.execute('UPDATE users SET reminder_count = reminder_count + 1 WHERE id = ?', (user,))

    # Parse schedule time and message
    message = message.replace('\n', newline_str)
    # TODO use relativedelta and allow month specification
    pattern = re.compile('[0-9]+[wdhm]')
    when_rel = pattern.findall(when)
    w, d, h, m = 0, 0, 0, 0
    if when_rel:
        for x in when_rel:
            try:
                if x[-1] == 'w':
                    w += int(x[:-1])
                elif x[-1] == 'd':
                    d += int(x[:-1])
                elif x[-1] == 'h':
                    h += int(x[:-1])
                elif x[-1] == 'm':
                    m += int(x[:-1])
            except:
                pass
        when = now + timedelta(weeks=w, days=d, hours=h, minutes=m)
    else:
        # TODO do smart parsing
        pass

    if when <= now:
        return
    if relativedelta(when, now).years > 1:
        await ctx.send('Reminders must be less than 2 years away.', delete_after=5)
        return
    when_fmt = when.strftime(dt_fmt)

    tz_notice = ''
    if not timezone:
        timezone = 'UTC'
        tz_notice = '\nYou have not set your timezone, so the reminder will be displayed with UTC time. You may update it with `$tz`.'
    timezone = pytz.timezone(timezone)

    channel = bot.get_channel(ctx.channel.id)
    if here and isinstance(channel, discord.DMChannel):
        here = False

    cur.execute('INSERT INTO reminders VALUES(?,?,?,?)',
        (when_fmt, user, channel.id if here else 0, message))
    when_fmt = when.astimezone(timezone).strftime(dt_fmt)

    log(f'Reminder set by {ctx.author.name} for {when_fmt} ({timezone.zone})')
    await ctx.send(f'Reminder set for {when_fmt}.{tz_notice}')
    con.commit()

@bot.command(aliases=['l','ls'])
async def list(ctx):
    cur = con.cursor()
    now = datetime.now(pytz.utc)
    user = ctx.author.id
    num = 0
    r_list = '```\n'

    cur.execute('SELECT timezone FROM users WHERE id = ?', (user,))
    timezone = cur.fetchone()
    if not timezone:
        return
    timezone = timezone[0]
    if not timezone:
        timezone = 'UTC'
    timezone = pytz.timezone(timezone)

    for when_fmt, channel, message in cur.execute('SELECT datetime,channel,message FROM reminders WHERE user = ? ORDER BY rowid ASC', (user,)):
        num += 1
        message = message.replace(newline_str, chr(8629))
        if len(message) > 32:
            message = message[:31] + '~'
        else:
            message = message.ljust(32)

        # Get localized datetime
        when = datetime.strptime(when_fmt, dt_fmt)
        when = when.astimezone(timezone)
        when_fmt = when.strftime(dt_fmt)

        # Get relative time
        rel = relativedelta(when, now)
        rel_parts = (
            (rel.years * 12, 'M'),
            (rel.weeks, 'w'),
            (rel.days - (rel.weeks * 7), 'd'),
            (rel.hours, 'h'),
            (rel.minutes, 'm'))
        rel = ''
        for part in rel_parts:
            if part[0]:
                rel += f'{part[0]}{part[1]} '
        rel = rel.strip()
        rel = rel.ljust(17)

        # TODO switch first and second row positions
        r_list += '┌──────────────────┬───────────────────┐\n'
        r_list += f'│ {when_fmt} | {rel} │\n'
        r_list += '├───╥──────────────┴───────────────────┤\n'
        r_list += f'│ {num} ║ {message} │\n'

        if channel:
            channel = bot.get_channel(channel)
            server = str(channel.guild)
            channel = str(channel)
            if len(channel) > 16:
                channel = channel[:15] + '~'
            else:
                channel = channel.ljust(16)
            if len(server) > 16:
                server = server[:15] + '~'
            else:
                server = server.ljust(16)
            r_list += '├───╨──────────────┬───────────────────┤\n'
            r_list += f'│ {server} | #{channel} │\n'
            r_list += '└──────────────────┴───────────────────┘\n'

        else:
            r_list += '└───╨──────────────────────────────────┘\n'

    if num:
        r_list += '```'
        await ctx.send(r_list)

@bot.command(aliases=['remove','del','rm'])
async def delete(ctx, which: int):
    cur = con.cursor()
    user = ctx.author.id

    cur.execute('SELECT rowid FROM reminders WHERE user = ? ORDER BY rowid ASC', (user,))
    rows = cur.fetchall()
    if not rows or which < 1 or which > len(rows):
        return

    # Select rowid from user's reminders
    which = rows[which-1][0]
    cur.execute('DELETE FROM reminders WHERE rowid = ?', (which,))
    cur.execute('UPDATE users SET reminder_count = reminder_count - 1 WHERE id = ?', (user,))

    log(f'Reminder deleted by {ctx.author.name}')
    await ctx.send('Reminder deleted.')
    con.commit()

@bot.command(aliases=['timezone'])
async def tz(ctx, new_timezone=None):
    cur = con.cursor()
    user = ctx.author.id
    howto = 'Select your timezone from this list, exactly as written:\nhttps://pastebin.com/raw/j2SAHX4r\n`$tz <timezone>`'
    cur.execute('SELECT timezone FROM users WHERE id = ?', (user,))
    timezone = cur.fetchone()

    # User does not exist
    if not timezone:
        create_user(user)
        timezone = ('',)

    if new_timezone:
        if new_timezone not in pytz.common_timezones:
            await ctx.send(howto)
        else:
            cur.execute('UPDATE users SET timezone = ? WHERE id = ?', (new_timezone, user))
            await ctx.send('Your current and future reminders will now use this timezone.')
            con.commit()

    else:
        timezone = timezone[0]
        if timezone:
            await ctx.send(f'Selected timezone: `{timezone}`')
        else: # Timezone not set
            await ctx.send(howto)


#########
# OTHER #
#########

# TODO
#@bot.command()
#async def cht(ctx, *query):
#    query = ['cht'] + list(query)
#    stdout = None
#    with subprocess.Popen(query, text=True, stdout=subprocess.PIPE) as proc:
#        stdout = proc.stdout.read()
#    if stdout:
#        max_len = 2000
#        ansi_escape = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')
#        stdout = ansi_escape.sub('', stdout)
#        if len(stdout) <= max_len-7:
#            await ctx.send(f'```\n{stdout}```')
#        else:
#            messages = [stdout]
#            while 1:
#                message = messages[-1][:max_len-6]
#                if len(message) == max_len-6:
#                    messages[-1] = messages[-1][:max_len-7]
#                    messages.append(message[:-1])
#                else:
#                    break
#            for x in messages:
#                await ctx.send(f'```\n{x}```')


##########
# HELPER #
##########

def log(message):
    now = datetime.now().strftime(dt_fmt + ':%S')
    message = f'[{now}] {message}'
    with open('log', 'a') as f:
        f.write(message + '\n')
    print(message)

def create_user(user):
    cur = con.cursor()
    cur.execute('INSERT INTO users VALUES(?,0,"")', (user,))
    con.commit()


with open('token') as f:
    bot.token = f.read()
check.start()
bot.run(bot.token)
