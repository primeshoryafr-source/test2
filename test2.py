import discord
from discord.ext import commands
import asyncio
import sqlite3
import os
import random

# Configuration
TOKEN = 'YOUR_BOT_TOKEN' # Apna Discord bot token dalein
ADMIN_ID = 123456789012345678 # Apna Discord User ID dalein
PREFIX = '!'

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# Database Setup (Added 'status' column for suspensions)
def init_db():
    conn = sqlite3.connect('docker_vps.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS vps
                 (user_id TEXT, container_name TEXT, ram TEXT, cpu TEXT, status TEXT DEFAULT 'active')''')
    conn.commit()
    conn.close()

init_db()

# Helper function to run shell commands asynchronously
async def run_cmd(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()

def check_suspension(container_name):
    conn = sqlite3.connect('docker_vps.db')
    c = conn.cursor()
    c.execute("SELECT status FROM vps WHERE container_name=?", (container_name,))
    result = c.fetchone()
    conn.close()
    return result[0] == 'suspended' if result else False

# ==========================================
# 🎛️ MANAGE PANEL UI
# ==========================================
class ManagePanel(discord.ui.View):
    def __init__(self, container_name, user_id):
        super().__init__(timeout=None)
        self.container_name = container_name
        self.user_id = str(user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("❌ This VPS does not belong to you!", ephemeral=True)
            return False
        
        if check_suspension(self.container_name):
            await interaction.response.send_message("⛔ **This VPS is suspended by an Administrator.** Contact support.", ephemeral=True)
            return False
            
        return True

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️")
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await run_cmd(f"docker start {self.container_name}")
        await interaction.followup.send(f"✅ VPS `{self.container_name}` has been started!", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await run_cmd(f"docker stop {self.container_name}")
        await interaction.followup.send(f"🛑 VPS `{self.container_name}` has been stopped!", ephemeral=True)

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await run_cmd(f"docker restart {self.container_name}")
        await interaction.followup.send(f"🔄 VPS `{self.container_name}` has been restarted!", ephemeral=True)

    @discord.ui.button(label="Tmate SSH", style=discord.ButtonStyle.primary, emoji="🔑")
    async def tmate_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⏳ Generating Tmate session... please wait. This might take up to 10 seconds.", ephemeral=True)
        
        code, out, err = await run_cmd(f"docker inspect -f '{{{{.State.Running}}}}' {self.container_name}")
        if "true" not in out:
            await interaction.edit_original_response(content="❌ Container is not running! Please start it first.")
            return
        
        await run_cmd(f"docker exec {self.container_name} apt-get update")
        await run_cmd(f"docker exec -e DEBIAN_FRONTEND=noninteractive {self.container_name} apt-get install tmate -y")
        
        await run_cmd(f"docker exec {self.container_name} pkill -f tmate")
        await run_cmd(f"docker exec {self.container_name} rm -f /tmp/tmate.sock")
        
        await run_cmd(f"docker exec {self.container_name} tmate -S /tmp/tmate.sock new-session -d")
        
        ssh_link = None
        for _ in range(5):
            await asyncio.sleep(2)
            code, out, err = await run_cmd(f"docker exec {self.container_name} tmate -S /tmp/tmate.sock display -p '#{{tmate_ssh}}'")
            if out and "@" in out:
                ssh_link = out
                break
        
        if ssh_link:
            try:
                await interaction.user.send(f"🔑 **SSH Access for `{self.container_name}`:**\n```bash\n{ssh_link}\n```\n⚠️ *Keep this link private!*")
                await interaction.edit_original_response(content="✅ Tmate SSH link has been sent to your DMs!")
            except discord.Forbidden:
                await interaction.edit_original_response(content="❌ Failed to send link. Please enable Direct Messages from server members!")
        else:
            await interaction.edit_original_response(content="❌ Error generating Tmate link. The Tmate servers might be busy or unreachable.")

class VPSDropdown(discord.ui.Select):
    def __init__(self, vps_list, user_id):
        self.user_id = str(user_id)
        options = []
        for v in vps_list:
            status_emoji = "⛔" if v[4] == 'suspended' else "🖥️"
            options.append(discord.SelectOption(label=v[1], description=f"RAM: {v[2]} | CPU: {v[3]} | Status: {v[4].capitalize()}", emoji=status_emoji))
            
        super().__init__(placeholder="Select a VPS to manage...", options=options)

    async def callback(self, interaction: discord.Interaction):
        container_name = self.values[0]
        
        if check_suspension(container_name):
            await interaction.response.send_message("⛔ **This VPS is suspended by an Administrator.** You cannot manage it right now.", ephemeral=True)
            return
            
        view = ManagePanel(container_name, self.user_id)
        embed = discord.Embed(
            title=f"⚙️ Managing VPS: {container_name}", 
            description="Use the buttons below to control your VPS.", 
            color=0x2b2d31
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ManageView(discord.ui.View):
    def __init__(self, vps_list, user_id):
        super().__init__(timeout=120)
        self.add_item(VPSDropdown(vps_list, user_id))

# ==========================================
# 🤖 BOT COMMANDS
# ==========================================
@bot.event
async def on_ready():
    print(f'✅ Bot is ready as {bot.user}')
    await bot.change_presence(activity=discord.Game(name="!help | Docker VPS"))

@bot.command(name='help')
async def help_cmd(ctx):
    embed = discord.Embed(title="📚 Docker VPS Bot Help Menu", color=0x5865F2)
    
    embed.add_field(name="👤 User Commands", value=
                    f"`{PREFIX}manage` - Manage your VPS (Start/Stop/Restart/SSH)\n"
                    f"`{PREFIX}vpsinfo` - Check the live status and usage of your VPS\n"
                    f"`{PREFIX}logs <vps_name>` - Get the last 20 terminal logs\n"
                    f"`{PREFIX}renamevps <old_name> <new_name>` - Rename your VPS\n"
                    f"`{PREFIX}ping` - Check bot latency", inline=False)
    
    if ctx.author.id == ADMIN_ID:
        embed.add_field(name="🛡️ Admin Commands", value=
                        f"`{PREFIX}deploy @user <ram> <cpu>` - Create a new VPS\n"
                        f"`{PREFIX}deletevps <vps_name>` - Forcefully delete a VPS\n"
                        f"`{PREFIX}listvps` - View all deployed VPS instances\n"
                        f"`{PREFIX}suspend <vps_name>` - Suspend a VPS\n"
                        f"`{PREFIX}unsuspend <vps_name>` - Unsuspend a VPS\n"
                        f"`{PREFIX}setram <vps_name> <ram>` - Change RAM dynamically\n"
                        f"`{PREFIX}cmd <vps_name> <command>` - Execute terminal command\n"
                        f"`{PREFIX}hoststats` - Check main server resource usage", inline=False)
    
    embed.set_footer(text="Docker VPS Management")
    await ctx.send(embed=embed)

@bot.command(name='ping')
async def ping_cmd(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 **Pong!** Bot Latency: `{latency}ms`")

# ----------------- USER COMMANDS -----------------

@bot.command(name='manage')
async def manage_vps(ctx):
    conn = sqlite3.connect('docker_vps.db')
    c = conn.cursor()
    c.execute("SELECT * FROM vps WHERE user_id=?", (str(ctx.author.id),))
    vps_list = c.fetchall()
    conn.close()

    if not vps_list:
        return await ctx.send("❌ You do not own any VPS. Please contact an admin.")

    embed = discord.Embed(
        title="🖥️ VPS Management Dashboard",
        description="Select your VPS from the dropdown menu below.",
        color=0x3498db
    )
    view = ManageView(vps_list, ctx.author.id)
    await ctx.send(embed=embed, view=view)

@bot.command(name='vpsinfo')
async def vps_info(ctx):
    conn = sqlite3.connect('docker_vps.db')
    c = conn.cursor()
    c.execute("SELECT container_name, status FROM vps WHERE user_id=?", (str(ctx.author.id),))
    vps_list = c.fetchall()
    conn.close()

    if not vps_list:
        return await ctx.send("❌ You do not own any VPS.")

    embed = discord.Embed(title="📊 Your VPS Status", color=0x9b59b6)
    msg = await ctx.send("Fetching live stats...")
    
    for v in vps_list:
        container_name = v[0]
        db_status = v[1]
        
        if db_status == 'suspended':
            embed.add_field(name=f"🖥️ {container_name}", value="**Status:** ⛔ Suspended", inline=False)
            continue
            
        code, out, err = await run_cmd(f"docker inspect -f '{{{{.State.Status}}}}' {container_name}")
        status = out if out else "Unknown"
        
        if status == "running":
            _, stats, _ = await run_cmd(f"docker stats --no-stream --format 'RAM Usage: {{{{ .MemUsage }}}} | CPU: {{{{ .CPUPerc }}}}' {container_name}")
            embed.add_field(name=f"🖥️ {container_name}", value=f"**Status:** 🟢 Running\n**Usage:** {stats}", inline=False)
        else:
            embed.add_field(name=f"🖥️ {container_name}", value=f"**Status:** 🔴 {status.capitalize()}", inline=False)

    await msg.edit(content=None, embed=embed)

@bot.command(name='logs')
async def get_logs(ctx, container_name: str):
    conn = sqlite3.connect('docker_vps.db')
    c = conn.cursor()
    c.execute("SELECT * FROM vps WHERE user_id=? AND container_name=?", (str(ctx.author.id), container_name))
    result = c.fetchone()
    conn.close()

    if not result:
        return await ctx.send("❌ VPS not found or you don't own it.")
    if result[4] == 'suspended':
        return await ctx.send("⛔ This VPS is suspended.")

    code, out, err = await run_cmd(f"docker logs --tail 20 {container_name}")
    log_data = err if err else out
    
    if len(log_data) > 1900:
        log_data = log_data[-1900:]
        
    await ctx.send(f"📜 **Last 20 Log Lines for `{container_name}`:**\n```bash\n{log_data if log_data else 'No logs found.'}\n```")

@bot.command(name='renamevps')
async def rename_vps(ctx, old_name: str, new_name: str):
    conn = sqlite3.connect('docker_vps.db')
    c = conn.cursor()
    c.execute("SELECT * FROM vps WHERE user_id=? AND container_name=?", (str(ctx.author.id), old_name))
    result = c.fetchone()

    if not result:
        conn.close()
        return await ctx.send("❌ VPS not found or you don't own it.")
    
    # Check if new name is alphanumeric to avoid injection
    if not new_name.isalnum() and "-" not in new_name:
        return await ctx.send("❌ Invalid name! Use only letters, numbers, and dashes.")

    code, out, err = await run_cmd(f"docker rename {old_name} {new_name}")
    if code == 0:
        c.execute("UPDATE vps SET container_name=? WHERE container_name=?", (new_name, old_name))
        conn.commit()
        await ctx.send(f"✅ Your VPS has been renamed from `{old_name}` to `{new_name}`.")
    else:
        await ctx.send(f"❌ Failed to rename VPS. Name might be already taken.\n```bash\n{err}\n```")
    conn.close()


# ----------------- ADMIN COMMANDS -----------------

@bot.command(name='deploy')
async def deploy_vps(ctx, member: discord.Member, ram: str, cpu: str):
    if ctx.author.id != ADMIN_ID:
        return await ctx.send("❌ This command is restricted to Admins!")

    if ram.isdigit():
        ram += "g"

    container_name = f"vps-{member.id}-{random.randint(1000, 9999)}"
    
    embed = discord.Embed(title="⏳ Deploying VPS...", description=f"Deploying Docker container for {member.mention}", color=0xf1c40f)
    msg = await ctx.send(embed=embed)

    try:
        cmd = f"docker run -itd --name {container_name} --memory {ram} --memory-swap {ram} --cpus {cpu} ubuntu-vps:latest /bin/bash"
        code, out, err = await run_cmd(cmd)

        if code == 0:
            conn = sqlite3.connect('docker_vps.db')
            c = conn.cursor()
            c.execute("INSERT INTO vps VALUES (?, ?, ?, ?, ?)", (str(member.id), container_name, ram, cpu, 'active'))
            conn.commit()
            conn.close()

            await run_cmd(f"docker exec {container_name} apt-get update")
            await run_cmd(f"docker exec -e DEBIAN_FRONTEND=noninteractive {container_name} apt-get install curl wget sudo htop -y")

            success_embed = discord.Embed(title="✅ VPS Deployed!", color=0x2ecc71)
            success_embed.add_field(name="Owner", value=member.mention, inline=True)
            success_embed.add_field(name="Container", value=f"`{container_name}`", inline=True)
            success_embed.add_field(name="Specs", value=f"RAM: {ram} | CPU: {cpu}", inline=False)
            success_embed.set_footer(text="Use !manage to control this VPS")
            
            await msg.edit(embed=success_embed)
        else:
            await msg.edit(content=f"❌ Error during deployment:\n```bash\n{err}\n```", embed=None)
            
    except Exception as e:
        await msg.edit(content=f"❌ System Error: {e}", embed=None)

@bot.command(name='listvps')
async def list_vps(ctx):
    if ctx.author.id != ADMIN_ID:
        return await ctx.send("❌ This command is restricted to Admins!")
        
    conn = sqlite3.connect('docker_vps.db')
    c = conn.cursor()
    c.execute("SELECT * FROM vps")
    all_vps = c.fetchall()
    conn.close()

    if not all_vps:
        return await ctx.send("ℹ️ No VPS instances deployed yet.")

    embed = discord.Embed(title="📋 All Deployed VPS Instances", color=0xe67e22)
    for v in all_vps:
        user = bot.get_user(int(v[0]))
        username = user.name if user else f"ID: {v[0]}"
        embed.add_field(name=f"📦 {v[1]}", value=f"👤 {username} | 💾 {v[2]} RAM | ⚙️ {v[3]} CPU | 📌 {v[4].capitalize()}", inline=False)
        
    await ctx.send(embed=embed)

@bot.command(name='deletevps')
async def delete_vps(ctx, container_name: str):
    if ctx.author.id != ADMIN_ID:
        return await ctx.send("❌ This command is restricted to Admins!")

    msg = await ctx.send(f"⏳ Deleting VPS `{container_name}`...")
    
    await run_cmd(f"docker stop {container_name}")
    await run_cmd(f"docker rm {container_name}")

    conn = sqlite3.connect('docker_vps.db')
    c = conn.cursor()
    c.execute("DELETE FROM vps WHERE container_name=?", (container_name,))
    conn.commit()
    conn.close()

    await msg.edit(content=f"🗑️ Successfully deleted VPS `{container_name}` from the system and database.")

@bot.command(name='suspend')
async def suspend_vps(ctx, container_name: str):
    if ctx.author.id != ADMIN_ID:
        return await ctx.send("❌ This command is restricted to Admins!")

    conn = sqlite3.connect('docker_vps.db')
    c = conn.cursor()
    c.execute("UPDATE vps SET status='suspended' WHERE container_name=?", (container_name,))
    
    if c.rowcount == 0:
        conn.close()
        return await ctx.send("❌ VPS not found in database.")
    
    conn.commit()
    conn.close()

    await run_cmd(f"docker stop {container_name}")
    await ctx.send(f"⛔ VPS `{container_name}` has been forcefully stopped and marked as **Suspended**.")

@bot.command(name='unsuspend')
async def unsuspend_vps(ctx, container_name: str):
    if ctx.author.id != ADMIN_ID:
        return await ctx.send("❌ This command is restricted to Admins!")

    conn = sqlite3.connect('docker_vps.db')
    c = conn.cursor()
    c.execute("UPDATE vps SET status='active' WHERE container_name=?", (container_name,))
    
    if c.rowcount == 0:
        conn.close()
        return await ctx.send("❌ VPS not found in database.")
    
    conn.commit()
    conn.close()

    await run_cmd(f"docker start {container_name}")
    await ctx.send(f"✅ VPS `{container_name}` is now **Active** and started.")

@bot.command(name='setram')
async def set_ram(ctx, container_name: str, ram: str):
    if ctx.author.id != ADMIN_ID:
        return await ctx.send("❌ This command is restricted to Admins!")
    
    if ram.isdigit():
        ram += "g"

    code, out, err = await run_cmd(f"docker update --memory {ram} --memory-swap {ram} {container_name}")
    
    if code == 0:
        conn = sqlite3.connect('docker_vps.db')
        c = conn.cursor()
        c.execute("UPDATE vps SET ram=? WHERE container_name=?", (ram, container_name))
        conn.commit()
        conn.close()
        await ctx.send(f"✅ Updated memory limit for `{container_name}` to **{ram}**.")
    else:
        await ctx.send(f"❌ Failed to update memory:\n```bash\n{err}\n```")

@bot.command(name='cmd')
async def execute_command(ctx, container_name: str, *, command: str):
    if ctx.author.id != ADMIN_ID:
        return await ctx.send("❌ This command is restricted to Admins!")
        
    code, out, err = await run_cmd(f"docker exec {container_name} sh -c '{command}'")
    result = out if out else err
    
    if len(result) > 1900:
        result = result[-1900:]
        
    await ctx.send(f"💻 **Output from `{container_name}`:**\n```bash\n{result if result else 'Command executed without output.'}\n```")

@bot.command(name='hoststats')
async def host_stats(ctx):
    if ctx.author.id != ADMIN_ID:
        return await ctx.send("❌ This command is restricted to Admins!")
        
    _, mem_out, _ = await run_cmd("free -m | awk 'NR==2{printf \"Memory Usage: %s/%sMB (%.2f%%)\\n\", $3,$2,$3*100/$2 }'")
    _, cpu_out, _ = await run_cmd("top -bn1 | grep load | awk '{printf \"CPU Load: %.2f\\n\", $(NF-2)}'")
    _, disk_out, _ = await run_cmd("df -h / | awk '$NF==\"/\"{printf \"Disk Usage: %d/%dGB (%s)\\n\", $3,$2,$5}'")
    
    embed = discord.Embed(title="🌐 Main Server (Host) Stats", color=0x1abc9c)
    embed.add_field(name="RAM", value=mem_out, inline=False)
    embed.add_field(name="CPU", value=cpu_out, inline=False)
    embed.add_field(name="Disk", value=disk_out, inline=False)
    
    await ctx.send(embed=embed)

# Run the Bot
bot.run(TOKEN)