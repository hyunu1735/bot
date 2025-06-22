import asyncio
 
import discord
import yt_dlp as youtube_dl
 
from discord.ext import commands
from dico_token import Token
 
# Suppress noise about console usage from errors
# youtube_dl.utils.bug_reports_message = lambda: ''
 
# <lambda>() got ~ 오류 발생 시 아래 사용
youtube_dl.utils.bug_reports_message = lambda *args, **kwargs: ''
 
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # bind to ipv4 since ipv6 addresses cause issues sometimes
}
 
ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}
 
ytdl = youtube_dl.YoutubeDL(ytdl_format_options)
 
# youtube 음악과 로컬 음악의 재생을 구별하기 위한 클래스 작성.
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
 
        self.data = data
 
        self.title = data.get('title')
        self.url = data.get('url')
 
    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
 
        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]
 
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
 
class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = []
        self.current_player = None
        self.max_queue_size = 10  # 대기열 최대 길이 설정
        self.loop = False
        self.loop_current = False
 
    async def play_next(self, ctx):
        if not ctx.voice_client:
            return
        
        try:
            if self.loop_current and self.current_player: 
                current_url = self.current_player.data['url']
                self.current_player = await YTDLSource.from_url(current_url, loop=self.bot.loop, stream=True)
                ctx.voice_client.play(self.current_player, after=lambda _: asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop))
                return
 
            if self.loop and self.current_player:
                current_url = self.current_player.data['url']
                current_title = self.current_player.title
                self.queue.append((current_url, current_title))
 
            if len(self.queue) > 0:
                # 다음 곡 재생
                next_url, next_title = self.queue.pop(0)
                self.current_player = await YTDLSource.from_url(next_url, loop=self.bot.loop, stream=True)
                ctx.voice_client.play(self.current_player, after=lambda _: asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop))
                
                # play_next에서 호출될 때는 메시지를 보내지 않음
                if ctx.command and ctx.command.name == "skip":
                    await ctx.send(f'지금 재생 중: {self.current_player.title}')
                    
        except Exception as e:
            await ctx.send(f"재생 중 오류가 발생했습니다: {str(e)}")
            print(f"재생 오류: {e}")
            # 오류 발생 시 대기열에 기존 재생 곡 추가
            if current_url and current_title:
                self.queue.insert(0, (current_url, current_title))
 
    async def search_youtube(self, query):
        ytdl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'default_search': 'ytsearch5',
            'extract_flat': True,
            'skip_download': True,
            'force_generic_extractor': True,
        }
 
        with youtube_dl.YoutubeDL(ytdl_opts) as ytdl:
            try:
                result = ytdl.extract_info(f"ytsearch5:{query}", download= False)
                if 'entries' in result:
                    # list comprehension
                    return [
                        (
                            f"https://www.youtube.com/watch?v={entry['id']}",
                            entry.get('title', 'N/A'),
                            f"{int(entry.get('duration', 0)) // 60}:{int(entry.get('duration', 0)) % 60:02d}" if entry.get('duration') else 'N/A'
                        )
                        for entry in result['entries']
                    ]
                return None
            except Exception as e:
                print(f"Search Error: {e}")
                return None
 
    @commands.command(aliases=["입장"])
    async def join(self, ctx):
        """음성 채널에 입장합니다"""
        if not ctx.author.voice:
            await ctx.send("음성 채널에 먼저 입장해주세요.")
            return
            
        channel = ctx.author.voice.channel
        
        if ctx.voice_client is not None:
            return await ctx.voice_client.move_to(channel)
 
        await channel.connect()
        await ctx.send(f"{channel} 채널에 입장했습니다.")
    
    @commands.command(aliases=["다음"])
    async def skip(self, ctx):
        """대기열에서 다음 곡을 재생합니다"""
        if not ctx.voice_client:
            await ctx.send("봇이 음성 채널에 연결되어 있지 않습니다.")
            return
            
        if not self.queue:  # 대기열이 비어있는지 확인
            await ctx.send("다음 재생할 곡이 대기열에 없습니다.\n음악을 계속 재생하시려면 음악을 추가해주세요.")
            return
            
        # 반복 모드에서 스킵 제한.
        if self.loop or self.loop_current:
            await ctx.send("반복 모드를 종료하고 skip 명령어를 입력해주세요.")
            return
 
        try:
            # 현재 재생중인 음악 중지
            if ctx.voice_client.is_playing():
                ctx.voice_client.stop()
                
            next_url, next_title = self.queue.pop(0)
            self.current_player = await YTDLSource.from_url(next_url, loop=self.bot.loop, stream=True)
 
            ctx.voice_client.play(self.current_player, after=lambda _: asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop))
            await ctx.send(f'지금 재생 중: {self.current_player.title}')
        except Exception as e:
            await ctx.send(f"재생 중 오류가 발생했습니다: {str(e)}")
            print(f"재생 오류: {e}")
            # 오류 발생 시 대기열에 기존 재생 곡 추가
            if next_url and next_title:
                self.queue.insert(0, (next_url, next_title))
   
    @commands.command(aliases=["재생"])
    async def play(self, ctx, *, query):
        """URL에서 음악을 재생하고 대기열에 추가합니다"""
        
        try:
            if not ctx.author.voice:
                await ctx.send("음성 채널에 먼저 입장해주세요.")
                return
 
            if not ctx.voice_client:
                await ctx.author.voice.channel.connect()
                
            if len(self.queue) >= self.max_queue_size:
                await ctx.send(f"대기열이 가득 찼습니다. 최대 {self.max_queue_size}곡까지만 추가할 수 있습니다.")
                return
            
            #youtube 검색
            if not query.startswith(('http://', 'https://')):
                async with ctx.typing():
                    url = await self.search_youtube(query)
                    if not url:
                        await ctx.send("검색 결과를 찾을 수 없습니다.")
                        return
            else:
                url = query            
 
            # 추가하려는 곡의 정보를 미리 가져옴
            async with ctx.typing():
                player = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
 
                if not ctx.voice_client.is_playing():
                    self.current_player = player
                    ctx.voice_client.play(self.current_player, after=lambda _: asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop))
                    await ctx.send(f'지금 재생 중: {self.current_player.title}')
                else:
                    self.queue.append((url, player.title))
                    queue_info = f'대기열에 "{player.title}" 노래가 추가되었습니다.\n현재 대기열 ({len(self.queue)}곡):\n'
                    for i, (_, title) in enumerate(self.queue, 1):
                        queue_info += f"{i}. {title}\n"
                    await ctx.send(queue_info)
                    
        except Exception as e:
            await ctx.send(f"음악을 추가하는 중 오류가 발생했습니다: {str(e)}")
            print(f"재생 오류: {e}")
 
    @commands.command(aliases=["음량","볼륨","소리"])
    async def volume(self, ctx, volume: int):
        """플레이어의 볼륨을 조절합니다"""
 
        if ctx.voice_client is None:
            return await ctx.send("음성 채널에 연결되어 있지 않습니다.")
 
        if not 0 <= volume <= 100:
            return await ctx.send("볼륨은 0에서 100 사이의 값이어야 합니다.")
 
        ctx.voice_client.source.volume = volume / 100
        await ctx.send(f"볼륨이 {volume}%로 변경되었습니다")
 
    @commands.command()
    async def stop(self, ctx):
        """재생을 멈추고 음성 채널에서 나갑니다"""
        if not ctx.voice_client:
            await ctx.send("봇이 음성 채널에 연결되어 있지 않습니다.")
            return
            
        self.queue.clear()
        self.loop_current = False
        self.loop = False
        self. current_player = None
 
        if ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        await ctx.voice_client.disconnect()
        await ctx.send("재생을 멈추고 채널에서 나갔습니다.")
        
    @commands.command()
    async def pause(self, ctx):
        """음악을 일시정지합니다"""
        if not ctx.voice_client:
            await ctx.send("봇이 음성 채널에 연결되어 있지 않습니다.")
            return
 
        if ctx.voice_client.is_paused() or not ctx.voice_client.is_playing():
            await ctx.send("음악이 이미 일시 정지 중이거나 재생 중이지 않습니다.")
            return
            
        ctx.voice_client.pause()
        await ctx.send("음악이 일시정지되었습니다.")
            
    @commands.command()
    async def resume(self, ctx):
        """일시정지된 음악을 다시 재생합니다"""
        if not ctx.voice_client:
            await ctx.send("봇이 음성 채널에 연결되어 있지 않습니다.")
            return
 
        if ctx.voice_client.is_playing() or not ctx.voice_client.is_paused():
            await ctx.send("음악이 이미 재생 중이거나 재생할 음악이 존재하지 않습니다.")
            return
            
        ctx.voice_client.resume()
        await ctx.send("음악이 다시 재생됩니다.")
 
    @commands.command(aliases=["q","플레이리스트","대기열"])
    async def queue(self, ctx):
        """현재 대기열을 보여줍니다"""
        if len(self.queue) == 0:
            await ctx.send("대기열이 비어있습니다.")
            return
            
        queue_list = f"현재 대기열 ({len(self.queue)}/{self.max_queue_size}곡):\n"
        for i, (_, title) in enumerate(self.queue, 1):
            queue_list += f"{i}. {title}\n"
        await ctx.send(queue_list)
 
    @commands.command()
    async def now(self, ctx):
        """현재 재생중인 음악의 제목을 보여줍니다"""
        if not ctx.voice_client or not ctx.voice_client.is_playing():
            await ctx.send("현재 재생 중인 음악이 없습니다.")
            return
            
        if self.current_player:
            await ctx.send(f"현재 재생 중: {self.current_player.title}")
        else:
            await ctx.send("현재 재생 중인 음악 정보를 가져올 수 없습니다.")
 
    @commands.command(aliases=["삭제", "제거"])
    async def remove(self, ctx, index: int):
        """대기열에서 특정 곡을 삭제합니다"""
        if len(self.queue) == 0:
            await ctx.send("대기열이 비어있습니다.")
            return
            
        if not 1 <= index <= len(self.queue):
            await ctx.send(f"올바른 번호를 입력해주세요. (1 ~ 대기열 길이`({len(self.queue)})`)")
            return
            
        _, removed_title = self.queue.pop(index-1)
        await ctx.send(f"대기열에서 {index}번 곡 '{removed_title}'이(가) 제거되었습니다.")
    
    @commands.command(aliases=['반복'])
    async def loop(self, ctx):
        """전체 곡을 반복 재생합니다."""
        if not ctx.voice_client:
            await ctx.send("음성 채널에 연결되어 있지 않습니다.")
            return
        
        self.loop = not self.loop
        self.loop_current = False # 한곡 반복과 전체 반복 구분
 
        if self.loop:
            await ctx.send("전체 곡 반복이 활성화되었습니다.")
        else:
            await ctx.send("전체 곡 반복이 비활성화되었습니다.")
 
    @commands.command(aliases=['한곡반복','현재곡반복'])
    async def loop_one(self, ctx):
        """현재 재생 중인 곡을 반복 재생합니다."""
        if not ctx.voice_client or not ctx.voice_client.is_playing():
            await ctx.send("현재 재생 중인 곡이 없거나 음성 채널에 연결되어 있지 않습니다.")
            return
        
        self.loop_current = not self.loop_current
        self.loop = False # 한곡 반복과 전체 반복 구분
 
        if self.loop_current:
            await ctx.send("현재 곡 반복이 활성화되었습니다.")
        else:
            await ctx.send("현재 곡 반복이 비활성화되었습니다.")
 
    @commands.command(aliases=['검색'])
    async def search(self, ctx, *, query):
        """유튜브에서 음악을 검색하여 리스트를 출력합니다."""
        try:
            if not ctx.author.voice:
                await ctx.send("음성 채널에 먼저 입장해주세요.")
                return
            
            searching_msg = await ctx.send("검색 중...")
 
            results = await self.search_youtube(query)
            if not results:
                await searching_msg.delete()
                await ctx.send("검색 결과를 찾을 수 없습니다.")
                return
                
            #검색 결과 표시
            embed = discord.Embed(
                title = "**검색 결과**",
                description = f"검색: {query} \n\n원하는 곡의 번호를 입력해주세요(1~5).\n'취소' 입력 시 검색을 취소합니다.",
                color = 0x3498db
            )
    
            for i, (_,title,duration) in enumerate(results, 1):
                embed.add_field(
                    name=f"{i}. ",
                    value=f"{title}\n재생 시간: {duration}",
                    inline = False
                )
            embed.set_footer(text="30초 내에 선택하지 않으면 자동 취소됩니다.")
 
            await searching_msg.delete()
            search_msg = await ctx.send(embed=embed)
 
            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel and \
                       (m.content.isdigit() or m.content.lower() == "취소")
 
            try:
                msg = await self.bot.wait_for('message', timeout=30.0, check=check)
            except asyncio.TimeoutError:
                await search_msg.delete()
                await ctx.send("시간이 초과되었습니다. 다시 검색해주세요.")
                return
 
            if msg.content.lower() == "취소":
                await search_msg.delete()
                await ctx.send("검색이 취소되었습니다.")
                return
 
            choice = int(msg.content)
            if not 1 <= choice <= len(results):
                await search_msg.delete()
                await ctx.send("올바른 번호를 입력해주세요.")
                return
            
            await search_msg.delete()
 
            selected_url = results[choice-1][0]
            selectd_title = results[choice-1][1]
            
            # 선택된 곡 재생
            if len(self.queue) >= self.max_queue_size:
                await ctx.send(f"대기열이 가득 찼습니다. 최대 {self.max_queue_size}곡까지만 추가할 수 있습니다.")
                return
 
            async with ctx.typing():
                player = await YTDLSource.from_url(selected_url, loop=self.bot.loop, stream=True)
 
                if not ctx.voice_client:
                    await ctx.author.voice.channel.connect()
 
                if not ctx.voice_client.is_playing():
                    self.current_player = player
                    ctx.voice_client.play(self.current_player, 
                                        after=lambda _: asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop))
                    await ctx.send(f'지금 재생 중: {selectd_title}')
                else:
                    self.queue.append((selected_url, selectd_title))
                    queue_info = f'대기열에 "{selectd_title}" 노래가 추가되었습니다.\n현재 대기열 ({len(self.queue)}곡):\n'
                    for i, (_, title) in enumerate(self.queue, 1):
                        queue_info += f"{i}. {title}\n"
                    await ctx.send(queue_info)
 
        except Exception as e:
            if 'search_msg' in locals():
                await search_msg.delete()
            await ctx.send(f"검색 중 오류가 발생했습니다: {str(e)}")
            print(f"검색 오류: {e}")
 
 
    @play.before_invoke
    @skip.before_invoke
    async def ensure_voice(self, ctx):
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send("음성 채널에 먼저 입장해주세요.")
                raise commands.CommandError("사용자가 음성 채널에 연결되어 있지 않습니다.")
 
 
intents = discord.Intents.default()
intents.message_content = True
 
bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("!"),
    description='Relatively simple music bot example',
    intents=intents,
)
 
 
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
 
 
async def main():
    async with bot:
        await bot.add_cog(Music(bot))
        await bot.start(Token)
 
 
asyncio.run(main())