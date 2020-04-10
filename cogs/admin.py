import collections
import logging

import discord.utils
import discord.ext.commands as commands

import paths
from utils import config, utils

log = logging.getLogger(__name__)


def setup(bot):
    bot.add_cog(Admin(bot))


class Admin(commands.Cog):
    """Bot management commands and events."""
    def __init__(self, bot):
        self.commands_used = collections.Counter()
        self.ignored = config.Config(paths.IGNORED_CONFIG, encoding='utf-8')
        self.bot = bot

    def bot_check_once(self, ctx):
        """A global check used on every command."""
        author = ctx.author
        guild = ctx.guild
        if author == ctx.bot.owner:
            return True

        if guild is not None:
            # Check if we're ignoring the guild
            if guild.id in self.ignored.guilds:
                return False

            if ctx.channel.id in self.ignored.channels:
                return False

            # Guild owners can't be ignored
            if author.id == guild.owner.id:
                return True

            # Check if the user is banned from using the bot
            if author.id in self.ignored.users.get(guild.id, {}):
                return False

            # Check if the channel is banned, bypass this if the user has the manage guild permission
            channel = ctx.channel
            perms = channel.permissions_for(author)
            if not perms.manage_guild and channel.id in self.ignored.channels:
                return False
        return True

    async def resolve_target(self, ctx, target):
        if target == 'channel':
            return ctx.channel, self.ignored.channels
        elif target == 'guild' or target == 'server':
            return ctx.guild, self.ignored.guilds

        # Try converting to a text channel
        try:
            channel = await commands.TextChannelConverter().convert(ctx, target)
        except commands.BadArgument:
            pass
        else:
            return channel, self.ignored.channels

        # Try converting to a user
        try:
            member = await commands.MemberConverter().convert(ctx, target)
        except commands.BadArgument:
            pass
        else:
            guild_id = ctx.guild.id
            try:
                return member, self.ignored.users[guild_id]
            except KeyError:
                self.ignored.users[guild_id] = {}
                return member, self.ignored.users[guild_id]

        # Convert to a guild
        try:
            guild = await utils.GuildConverter().convert(ctx, target)
        except:
            pass
        else:
            return guild, self.ignored.guilds

        # Nope
        raise commands.BadArgument(f'"{target}" not found.')

    def validate_ignore_target(self, ctx, target):
        owner_id = ctx.bot.owner.id
        # Only let the bot owner unignore a guild owner
        if isinstance(target, discord.Member):
            # Do not ignore the bot owner
            if owner_id == target.id:
                raise commands.BadArgument('Cannot ignore/unignore the bot owner.')

            # Only allow the bot owner to unignore the guild owner
            if target.id == ctx.guild.owner.id and ctx.author.id != owner_id:
                raise commands.BadArgument('Only the bot owner can ignore/unignore the owner of a server.')
        elif isinstance(target, discord.Guild):
            # Only allow the bot owner to ignore guilds
            if ctx.author.id != owner_id:
                raise commands.BadArgument('Only the bot owner can ignore/unignore servers.')
        elif isinstance(target, discord.VoiceChannel):
            # Do not ignore voice channels
            raise commands.BadArgument('Cannot ignore/unignore voice channels.')

    @commands.command(aliases=['checkpermissions'])
    @commands.guild_only()
    async def checkperms(self, ctx):
        name = ctx.bot.user.name
        perms_str = 'Read Messages, Send Messages, Manage Messages, Embed Links, Read Message History, Use External Emojis, Add Reactions'
        perms = discord.Permissions(486464)

        # Check the integration role
        role = discord.utils.get(ctx.guild.roles, name=name)
        if not role or not perms <= role.permissions:
            raise commands.UserInputError(f'Please make sure the integration role `{name}` has all the following permissions and is added to the bot :\n'
                                          f'{perms_str}.\n'
                                          f'Note that you can also kick and re-invite the bot with its default permissions.')

        # Check every channel for overwrites
        failed = []
        for channel in sorted(ctx.guild.text_channels, key=lambda c: c.position):
            if not perms <= channel.permissions_for(ctx.guild.me):
                failed.append(channel)
        if failed:
            raise commands.UserInputError(f'Please make sure the channel permissions overwrites for the following channels do not remove these permissions from the bot :\n'
                                          f'{perms_str}.\n'
                                          f'{" ".join(c.mention for c in failed)}')

        await ctx.send('All good.')

    @commands.group(name='ignore', invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def ignore_group(self, ctx, target, *, reason):
        """Ignores a channel, a user (server-wide), or a whole server.

        The target can be a name, an ID, the keyword 'channel' or 'server'.
        """
        target, conf = await self.resolve_target(ctx, target)
        self.validate_ignore_target(ctx, target)

        # Save the ignore
        conf[target.id] = reason
        self.ignored.save()

        # Leave the server or acknowledge the ignore being successful
        if isinstance(target, discord.Guild):
            await target.leave()

        await ctx.message.add_reaction('\N{WHITE HEAVY CHECK MARK}')

    @ignore_group.command(name='list')
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def ignore_list(self, ctx):
        """Lists ignored channels, users and servers related to the command's use."""
        channels = {discord.utils.get(ctx.guild.text_channels, id=cid): reason for cid, reason in self.ignored.channels.items()}
        members = {discord.utils.get(ctx.guild.members, id=uid): reason for uid, reason in self.ignored.users.get(ctx.guild.id, {}).items()}

        embed = discord.Embed(colour=discord.Colour.blurple())
        embed.add_field(name='Ignored channels', value='\n'.join(f'{c.mention}: {r}' for c, r in channels.items() if c is not None) or 'None', inline=False)
        embed.add_field(name='Ignored users', value='\n'.join(f'{m.mention}: {r}' for m, r in members.items() if m is not None) or 'None', inline=False)

        if ctx.author == ctx.bot.owner:
            embed.add_field(name='Ignored guilds', value='\n'.join(f'{g}: {r}' for g, r in self.ignored.guilds) or 'None', inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def unignore(self, ctx, *, target):
        """Un-ignores a channel, a user (server-wide), or a whole server."""
        target, conf = await self.resolve_target(ctx, target)
        self.validate_ignore_target(ctx, target)

        try:
            del conf[target.id]
        except ValueError:
            await ctx.send('Target not found.')
        else:
            if isinstance(target, discord.Member) and len(conf) == 0:
                del self.ignored.users[ctx.guild.id]
            self.ignored.save()
            await ctx.message.add_reaction('\N{WHITE HEAVY CHECK MARK}')

    @commands.command()
    @commands.is_owner()
    async def restart(self, ctx):
        """Restarts the bot."""
        ctx.bot.restart()

    @commands.command()
    @commands.is_owner()
    async def shutdown(self, ctx):
        """Shuts the bot down."""
        ctx.bot.shutdown()

    @commands.command()
    @commands.is_owner()
    async def status(self, ctx, *, status=None):
        """Changes the bot's status."""
        await ctx.bot.change_presence(game=discord.Game(name=status))
        self.bot.conf.status = status
        self.bot.conf.save()

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx, member, *, reason: utils.AuditLogReason):
        """Bans a member by name, mention or ID."""
        try:
            member_id = int(member)
        except ValueError:
            member = await commands.MemberConverter().convert(ctx, member) # Let this raise on failure
            await member.ban(reason=reason)
        else:
            await ctx.guild.ban(discord.Object(id=member_id), reason=reason)
        await ctx.message.add_reaction('\N{WHITE HEAVY CHECK MARK}')

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, member, *, reason: utils.AuditLogReason):
        """Unbans a member by name or ID."""
        bans = await ctx.guild.bans()
        try:
            member_id = int(member, base=10)
        except ValueError:
            ban_entry = discord.utils.get(bans, user__name=member)
        else:
            ban_entry = discord.utils.get(bans, user__id=member_id)

        if not ban_entry:
            raise commands.BadArgument(f'Banned member "{member}" not found.')

        await ctx.guild.unban(ban_entry.user, reason=reason)
        await ctx.message.add_reaction('\N{WHITE HEAVY CHECK MARK}')

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def softban(self, ctx, member: discord.Member, *, reason: utils.AuditLogReason(details='softban')):
        """Softbans a member by name, mention or ID.

        A softban is the action of banning a member and immediately unbanning them.
        It results in a kick that cleared their last day's message.
        """
        await member.ban(reason=reason)
        await member.unban(reason=reason)
        await ctx.message.add_reaction('\N{WHITE HEAVY CHECK MARK}')

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason: utils.AuditLogReason):
        """Kicks a member by name, mention or ID."""
        await member.kick(reason=reason)
        await ctx.message.add_reaction('\N{WHITE HEAVY CHECK MARK}')

    @commands.Cog.listener()
    async def on_command(self, ctx):
        self.commands_used[ctx.command.qualified_name] += 1
        if ctx.guild is None:
            log.info(f'DM:{ctx.author.name}:{ctx.author.id}:{ctx.message.content}')
        else:
            log.info(f'{ctx.guild.name}:{ctx.guild.id}:{ctx.channel.name}:{ctx.channel.id}:{ctx.author.name}:{ctx.author.id}:{ctx.message.content}')

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        # Log that the bot has been added somewhere
        log.info(f'GUILD_JOIN:{guild.name}:{guild.id}:{guild.owner.name}:{guild.owner.id}:')
        if guild.id in self.ignored.guilds:
            log.info(f'IGNORED GUILD:{guild.name}:{guild.id}:')
            await guild.leave()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        # Log that the bot has been removed from somewhere
        log.info(f'GUILD_REMOVE:{guild.name}:{guild.id}:{guild.owner.name}:{guild.owner.id}:')
