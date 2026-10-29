using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;

namespace AiReportDesktop;

// One connection per desktop process. Credentials never leave memory or their API origin.
internal static class BackendConnection
{
    private static readonly string SettingsPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AIassist", "backend.json");
    public static string LocalUrl { get; private set; } = "http://127.0.0.1:8000/api";
    public static string ServerUrl { get; private set; } = "http://10.60.150.179:8000/api";
    public static bool UseServer { get; private set; } = true;
    public static string BaseUrl => UseServer ? ServerUrl : LocalUrl;
    public static string? Token { get; set; }
    private static int _activeRequests;
    public static bool HasActiveRequests => Volatile.Read(ref _activeRequests) != 0;
    public static event Action? Unauthorized;
    public static readonly HttpClient Client = new(new SessionHandler()) { Timeout = TimeSpan.FromMinutes(10) };

    static BackendConnection()
    {
        try
        {
            if (File.Exists(SettingsPath))
            {
                using var doc = JsonDocument.Parse(File.ReadAllText(SettingsPath));
                LocalUrl = Normalize(doc.RootElement.GetProperty("localUrl").GetString()!);
                ServerUrl = Normalize(doc.RootElement.GetProperty("serverUrl").GetString()!);
                UseServer = doc.RootElement.GetProperty("useServer").GetBoolean();
            }
        }
        catch (Exception ex) when (ex is IOException or JsonException or ArgumentException or InvalidOperationException or KeyNotFoundException) { }
        var configured = Environment.GetEnvironmentVariable("AIASSIST_BACKEND_URL");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            ServerUrl = Normalize(configured);
            UseServer = true;
        }
    }

    public static string Normalize(string value)
    {
        if (!Uri.TryCreate(value.Trim(), UriKind.Absolute, out var uri) ||
            (uri.Scheme != "http" && uri.Scheme != "https") ||
            !string.IsNullOrEmpty(uri.UserInfo) || !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment))
            throw new ArgumentException("请填写有效的 HTTP/HTTPS API 地址，不能包含密码或查询参数。");
        var url = uri.AbsoluteUri.TrimEnd('/');
        return url.EndsWith("/api", StringComparison.OrdinalIgnoreCase) ? url : url + "/api";
    }

    public static void Configure(bool useServer, string localUrl, string serverUrl)
    {
        if (HasActiveRequests) throw new InvalidOperationException("请求正在执行，请稍后切换后端。");
        var local = Normalize(localUrl);
        var server = Normalize(serverUrl);
        Directory.CreateDirectory(Path.GetDirectoryName(SettingsPath)!);
        File.WriteAllText(SettingsPath, JsonSerializer.Serialize(new { localUrl = local, serverUrl = server, useServer }));
        Token = null;
        LocalUrl = local;
        ServerUrl = server;
        UseServer = useServer;
    }

    public static bool IsApiUrl(Uri uri)
    {
        var root = new Uri(BaseUrl + "/");
        return uri.Scheme == root.Scheme && uri.Authority == root.Authority &&
               (uri.AbsolutePath == root.AbsolutePath.TrimEnd('/') || uri.AbsolutePath.StartsWith(root.AbsolutePath, StringComparison.Ordinal));
    }

    // Probe a candidate without changing the active endpoint or its session.
    public static async Task<bool> ValidateEndpointAsync(string url, bool allowLegacyLocal, string? token,
        CancellationToken cancellationToken = default)
    {
        using var client = CreateEndpointClient(url, token);
        using var health = await client.GetAsync("health", cancellationToken);
        health.EnsureSuccessStatusCode();
        using var me = await client.GetAsync("auth/me", cancellationToken);
        if (me.StatusCode == HttpStatusCode.Unauthorized) return false;
        if (me.StatusCode == HttpStatusCode.NotFound && allowLegacyLocal) return true;
        me.EnsureSuccessStatusCode();
        return true;
    }

    public static HttpClient CreateEndpointClient(string url, string? token = null)
    {
        var client = new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false })
        {
            BaseAddress = new Uri(Normalize(url) + "/"),
            Timeout = TimeSpan.FromSeconds(30)
        };
        if (!string.IsNullOrEmpty(token)) client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return client;
    }

    private sealed class SessionHandler : DelegatingHandler
    {
        public SessionHandler() : base(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false }) { }
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            if (request.RequestUri == null || !IsApiUrl(request.RequestUri))
                throw new InvalidOperationException("已阻止向当前后端以外的地址发送请求。");
            Interlocked.Increment(ref _activeRequests);
            var token = Token;
            try
            {
                if (!string.IsNullOrEmpty(token)) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
                var response = await base.SendAsync(request, cancellationToken);
                if (response.StatusCode == HttpStatusCode.Unauthorized && token != null && token == Token)
                {
                    Token = null;
                    Unauthorized?.Invoke();
                }
                return response;
            }
            finally { Interlocked.Decrement(ref _activeRequests); }
        }
    }
}
