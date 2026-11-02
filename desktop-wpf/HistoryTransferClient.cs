using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace AiReportDesktop;

/// <summary>A bearer session belongs to exactly one API origin; credentials are never persisted.</summary>
public sealed class BackendSession : IDisposable
{
    private const int SmallResponseLimit = 2 * 1024 * 1024;
    public Uri ApiBase { get; }
    public HttpClient Client { get; }
    public string? UserId { get; private set; }
    public string? Username { get; private set; }
    public string? Role { get; private set; }
    public bool IsAuthenticated => UserId != null && Client.DefaultRequestHeaders.Authorization != null;

    public BackendSession(Uri apiBase, HttpMessageHandler? handler = null)
    {
        if (!apiBase.IsAbsoluteUri || apiBase.Scheme is not ("http" or "https")
            || !string.IsNullOrEmpty(apiBase.UserInfo) || !string.IsNullOrEmpty(apiBase.Query)
            || !string.IsNullOrEmpty(apiBase.Fragment) || apiBase.AbsolutePath.TrimEnd('/') != "/api")
            throw new ArgumentException("后端地址必须是包含 /api 的 HTTP 或 HTTPS 地址。", nameof(apiBase));
        ApiBase = new Uri(apiBase.GetLeftPart(UriPartial.Path).TrimEnd('/'));
        Client = new HttpClient(handler ?? new HttpClientHandler { AllowAutoRedirect = false }, disposeHandler: true)
        {
            BaseAddress = new Uri(ApiBase + "/"),
            Timeout = TimeSpan.FromMinutes(10),
        };
    }

    public async Task LoginAsync(string username, string password, CancellationToken ct = default)
    {
        ClearAuthentication();
        using var response = await Client.PostAsJsonAsync(ResolveApiUri("auth/login"),
            new { username = username.Trim(), password }, ct);
        await EnsureSuccessAsync(response, ct);
        using var document = await ReadJsonAsync(response, SmallResponseLimit, ct);
        var root = document.RootElement;
        var token = RequiredString(root, "access_token");
        if (!Regex.IsMatch(token, "^[A-Za-z0-9_-]{43}$") || !root.TryGetProperty("user", out var user))
            throw new InvalidDataException("登录响应不完整，请确认后端版本。");
        var userId = RequiredString(user, "id");
        if (!Guid.TryParse(userId, out _)) throw new InvalidDataException("登录响应的用户编号无效。");
        var account = RequiredString(user, "username");
        var role = RequiredString(user, "role");
        if (role is not ("admin" or "user")) throw new InvalidDataException("登录响应的账号角色无效。");
        UserId = userId;
        Username = account;
        Role = role;
        Client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
    }

    public async Task RegisterAsync(string username, string password, string? displayName = null, CancellationToken ct = default)
    {
        using var response = await Client.PostAsJsonAsync(ResolveApiUri("auth/register"),
            new { username = username.Trim(), password, display_name = string.IsNullOrWhiteSpace(displayName) ? null : displayName.Trim() }, ct);
        await EnsureSuccessAsync(response, ct);
    }

    public async Task LogoutAsync(CancellationToken ct = default)
    {
        try
        {
            if (IsAuthenticated)
            {
                using var response = await Client.PostAsync(ResolveApiUri("auth/logout"), null, ct);
                await EnsureSuccessAsync(response, ct);
            }
        }
        finally { ClearAuthentication(); }
    }

    public void ClearAuthentication()
    {
        Client.DefaultRequestHeaders.Authorization = null;
        UserId = Username = Role = null;
    }

    public Uri ResolveApiUri(string path)
    {
        if (string.IsNullOrWhiteSpace(path)) throw new ArgumentException("请求地址为空。", nameof(path));
        // A leading slash is an API path, not a Windows file URI.
        var target = path.StartsWith('/')
            ? new Uri(ApiBase, path)
            : Uri.TryCreate(path, UriKind.Absolute, out var absolute)
                ? absolute : new Uri(new Uri(ApiBase + "/"), path);
        if (!IsSameOrigin(target, ApiBase) || !string.IsNullOrEmpty(target.UserInfo)
            || !target.AbsolutePath.StartsWith(ApiBase.AbsolutePath + "/", StringComparison.Ordinal))
            throw new InvalidOperationException("拒绝访问当前后端之外的文件地址。");
        return target;
    }

    public static bool IsSameOrigin(Uri first, Uri second) =>
        first.Scheme.Equals(second.Scheme, StringComparison.OrdinalIgnoreCase)
        && first.IdnHost.Equals(second.IdnHost, StringComparison.OrdinalIgnoreCase) && first.Port == second.Port;

    public async Task DownloadFileAsync(string relativePath, string targetPath, CancellationToken ct = default)
    {
        RequireAuthenticated();
        var address = ResolveApiUri(relativePath);
        var fullPath = Path.GetFullPath(targetPath);
        var parent = Path.GetDirectoryName(fullPath) ?? throw new ArgumentException("请选择保存位置。", nameof(targetPath));
        if (!Directory.Exists(parent)) throw new DirectoryNotFoundException("保存目录不存在。");
        var temporary = Path.Combine(parent, ".aiassist-download-" + Guid.NewGuid().ToString("N") + ".tmp");
        try
        {
            using var response = await Client.GetAsync(address, HttpCompletionOption.ResponseHeadersRead, ct);
            await EnsureSuccessAsync(response, ct);
            await using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, useAsync: true))
            await using (var input = await response.Content.ReadAsStreamAsync(ct))
            {
                await CopyBoundedAsync(input, output, 128L * 1024 * 1024, ct);
                await output.FlushAsync(ct);
            }
            ct.ThrowIfCancellationRequested();
            // Same-directory rename preserves an existing destination until the download is complete.
            File.Move(temporary, fullPath, overwrite: true);
        }
        finally
        {
            if (File.Exists(temporary)) File.Delete(temporary);
        }
    }

    internal void RequireAuthenticated()
    {
        if (!IsAuthenticated) throw new InvalidOperationException("请先登录对应的后端账号。");
    }

    internal async Task EnsureSuccessAsync(HttpResponseMessage response, CancellationToken ct)
    {
        if (response.IsSuccessStatusCode) return;
        if (response.StatusCode == HttpStatusCode.Unauthorized) ClearAuthentication();
        var detail = response.StatusCode switch
        {
            HttpStatusCode.Unauthorized => "登录已失效，请重新登录对应的后端账号。",
            HttpStatusCode.Forbidden => "当前账号无权执行此操作，请检查账号角色和后端地址。",
            HttpStatusCode.NotFound => "记录不存在或未分配给当前账号。",
            HttpStatusCode.Gone => "该记录已被管理员撤回。",
            HttpStatusCode.Conflict => "记录内容发生冲突，请刷新收件箱后重试。",
            _ => $"后端请求失败（HTTP {(int)response.StatusCode}）。",
        };
        try
        {
            using var body = await ReadJsonAsync(response, 64 * 1024, ct);
            if (body.RootElement.TryGetProperty("detail", out var message) && message.ValueKind == JsonValueKind.String
                && message.GetString() is { Length: > 0 and <= 400 } text)
                detail = text;
        }
        catch (Exception ex) when (ex is JsonException or InvalidDataException or HttpRequestException) { }
        throw new BackendRequestException(detail, response.StatusCode);
    }

    internal static async Task<JsonDocument> ReadJsonAsync(HttpResponseMessage response, int maxBytes, CancellationToken ct)
        => JsonDocument.Parse(await ReadBoundedAsync(response.Content, maxBytes, ct));

    internal static async Task<byte[]> ReadBoundedAsync(HttpContent content, int maxBytes, CancellationToken ct)
    {
        if (content.Headers.ContentLength > maxBytes) throw new InvalidDataException("传输内容超过允许大小，请按审查批次接收。");
        await using var input = await content.ReadAsStreamAsync(ct);
        using var output = new MemoryStream();
        await CopyBoundedAsync(input, output, maxBytes, ct);
        return output.ToArray();
    }

    private static async Task CopyBoundedAsync(Stream input, Stream output, long maxBytes, CancellationToken ct)
    {
        var buffer = new byte[81920];
        long total = 0;
        int count;
        while ((count = await input.ReadAsync(buffer.AsMemory(), ct)) != 0)
        {
            total += count;
            if (total > maxBytes) throw new InvalidDataException("传输内容超过允许大小，请按审查批次接收。");
            await output.WriteAsync(buffer.AsMemory(0, count), ct);
        }
    }

    internal static string RequiredString(JsonElement root, string name)
        => root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(value.GetString())
            ? value.GetString()! : throw new InvalidDataException("后端响应缺少必要字段：" + name);

    public void Dispose() { ClearAuthentication(); Client.Dispose(); }
}

public sealed class BackendRequestException(string message, HttpStatusCode statusCode) : HttpRequestException(message, null, statusCode);

public sealed record HistoryDelivery(string Id, string PackageSha256, string Status, string ProjectId, DateTimeOffset CreatedAt);
public sealed record HistoryReceiveResult(string LocalProjectId, bool Duplicate, bool Acknowledged, string Message);

/// <summary>Receive complete packages into local storage before acknowledging them to the server.</summary>
public sealed class HistoryTransferClient
{
    private const int MaximumPackageBytes = 48 * 1024 * 1024;
    private readonly BackendSession _server;
    private readonly BackendSession _local;

    public HistoryTransferClient(BackendSession serverSession, BackendSession localSession)
    {
        if (!IsLocalBackend(localSession.ApiBase)) throw new ArgumentException("接收目标必须是本机后端（localhost、127.0.0.1 或 ::1）。");
        if (BackendSession.IsSameOrigin(serverSession.ApiBase, localSession.ApiBase))
            throw new ArgumentException("服务器地址与本地接收地址不能相同。");
        _server = serverSession;
        _local = localSession;
    }

    public static bool IsLocalBackend(Uri address)
    {
        var host = address.IdnHost.Trim('[', ']');
        return host.Equals("localhost", StringComparison.OrdinalIgnoreCase)
            || IPAddress.TryParse(host, out var ip) && IPAddress.IsLoopback(ip);
    }

    public async Task<IReadOnlyList<HistoryDelivery>> ListInboxAsync(int offset = 0, int limit = 50,
        string? status = null, CancellationToken ct = default)
    {
        _server.RequireAuthenticated();
        if (offset < 0 || limit is < 1 or > 100) throw new ArgumentOutOfRangeException(nameof(limit));
        if (status is not (null or "pending" or "received" or "revoked")) throw new ArgumentException("收件状态无效。", nameof(status));
        var path = $"history/inbox?offset={offset}&limit={limit}" + (status == null ? "" : "&status=" + status);
        using var response = await _server.Client.GetAsync(_server.ResolveApiUri(path), HttpCompletionOption.ResponseHeadersRead, ct);
        await _server.EnsureSuccessAsync(response, ct);
        using var body = await BackendSession.ReadJsonAsync(response, 2 * 1024 * 1024, ct);
        if (!body.RootElement.TryGetProperty("items", out var items) || items.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException("收件箱响应格式无效。");
        return items.EnumerateArray().Select(item => new HistoryDelivery(
            RequiredUuid(item, "id"), RequiredDigest(item, "package_sha256"), BackendSession.RequiredString(item, "status"),
            RequiredUuid(item, "project_id"), DateTimeOffset.Parse(BackendSession.RequiredString(item, "created_at"), CultureInfo.InvariantCulture))).ToArray();
    }

    public async Task<HistoryReceiveResult> ReceiveAsync(HistoryDelivery delivery, CancellationToken ct = default)
    {
        _server.RequireAuthenticated();
        _local.RequireAuthenticated();
        if (!Guid.TryParse(delivery.Id, out _) || !Regex.IsMatch(delivery.PackageSha256, "^[0-9a-f]{64}$"))
            throw new InvalidDataException("收件编号或校验值无效，请刷新收件箱。");
        if (delivery.Status == "revoked") throw new InvalidOperationException("该记录已被管理员撤回。");
        using var download = await _server.Client.GetAsync(_server.ResolveApiUri("history/inbox/" + delivery.Id + "/package"),
            HttpCompletionOption.ResponseHeadersRead, ct);
        await _server.EnsureSuccessAsync(download, ct);
        var bytes = await BackendSession.ReadBoundedAsync(download.Content, MaximumPackageBytes, ct);
        var digest = Convert.ToHexStringLower(SHA256.HashData(bytes));
        if (!download.Headers.TryGetValues("X-Package-SHA256", out var values)
            || values.SingleOrDefault() != digest || digest != delivery.PackageSha256)
            throw new InvalidDataException("服务器文件校验失败，尚未写入本地，请刷新后重新接收。");

        using var package = JsonDocument.Parse(bytes);
        if (package.RootElement.ValueKind != JsonValueKind.Object) throw new InvalidDataException("服务器传输包格式无效。");
        // Embed validated UTF-8 bytes unchanged: no floating-point conversion or escaping expansion.
        // The local backend verifies canonical contents and each attachment before committing anything.
        var prefix = Encoding.UTF8.GetBytes("{\"delivery_id\":\"" + delivery.Id + "\",\"package_sha256\":\"" + digest + "\",\"package\":");
        var envelope = new byte[prefix.Length + bytes.Length + 1];
        prefix.CopyTo(envelope, 0);
        bytes.CopyTo(envelope, prefix.Length);
        envelope[^1] = (byte)'}';
        if (envelope.Length > 50 * 1024 * 1024) throw new InvalidDataException("本地导入请求超过允许大小。");
        using var importRequest = new HttpRequestMessage(HttpMethod.Post, _local.ResolveApiUri("history/imports"))
        {
            Content = new ByteArrayContent(envelope),
        };
        importRequest.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json") { CharSet = "utf-8" };
        using var imported = await _local.Client.SendAsync(importRequest, HttpCompletionOption.ResponseHeadersRead, ct);
        await _local.EnsureSuccessAsync(imported, ct);
        using var receipt = await BackendSession.ReadJsonAsync(imported, 2 * 1024 * 1024, ct);
        var localProjectId = RequiredUuid(receipt.RootElement, "project_id");
        if (RequiredDigest(receipt.RootElement, "package_sha256") != digest
            || RequiredUuid(receipt.RootElement, "delivery_id") != delivery.Id)
            throw new InvalidDataException("本地导入回执不匹配，尚未向服务器确认；请重试接收以核对已保存记录。");
        var duplicate = receipt.RootElement.TryGetProperty("duplicate", out var duplicateValue) && duplicateValue.ValueKind == JsonValueKind.True;
        try
        {
            using var acknowledgement = await _server.Client.PostAsJsonAsync(
                _server.ResolveApiUri("history/inbox/" + delivery.Id + "/ack"), new { package_sha256 = digest }, ct);
            await _server.EnsureSuccessAsync(acknowledgement, ct);
            using var ack = await BackendSession.ReadJsonAsync(acknowledgement, 2 * 1024 * 1024, ct);
            if (RequiredUuid(ack.RootElement, "id") != delivery.Id || RequiredDigest(ack.RootElement, "package_sha256") != digest
                || BackendSession.RequiredString(ack.RootElement, "status") != "received")
                throw new InvalidDataException("服务器接收确认无效。");
            return new(localProjectId, duplicate, true, duplicate ? "本地已有此记录，已确认接收。" : "审查记录和原始文件已保存到本地。");
        }
        catch (Exception ex) when (ex is HttpRequestException or OperationCanceledException or InvalidDataException or JsonException)
        {
            // A failed/cancelled acknowledgement must never be reported as lost local data.
            return new(localProjectId, duplicate, false, "记录和原始文件已保存到本地，服务器确认尚未完成；重新登录后重试接收即可，不会重复导入。");
        }
    }

    private static string RequiredUuid(JsonElement root, string name)
    {
        var value = BackendSession.RequiredString(root, name);
        return Guid.TryParse(value, out _) ? value : throw new InvalidDataException("后端响应的记录编号无效。");
    }

    private static string RequiredDigest(JsonElement root, string name)
    {
        var value = BackendSession.RequiredString(root, name);
        return Regex.IsMatch(value, "^[0-9a-f]{64}$") ? value : throw new InvalidDataException("后端响应的文件校验值无效。");
    }
}
