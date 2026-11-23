using AiReportDesktop;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

var cases = new (string Name, Func<Task> Run)[]
{
    ("invalid_credentials_fail_before_network", Tests.InvalidCredentials),
    ("credential_boundaries_match_backend", Tests.CredentialBoundaries),
    ("legacy_short_login_password_is_preserved", Tests.LegacyLogin),
    ("validation_array_explains_fields_without_secrets", Tests.ValidationArray),
    ("validation_array_missing_fields_is_safe", Tests.ValidationArrayFallback),
    ("backend_string_error_is_preserved", Tests.StringError),
    ("real_session_tokens_are_separate", Tests.SeparateSessions),
    ("receive_keeps_utf8_and_number_tokens", Tests.Success),
    ("memory_package_keeps_full_payload", Tests.MemoryPackage),
    ("v3_profile_feedback_keeps_full_payload", Tests.WorkspaceMemoryPackage),
    ("v3_actual_backend_fixture_is_forwarded_unchanged", Tests.ActualWorkspaceMemoryFixture),
    ("v3_rejected_by_v2_backend_before_import", Tests.WorkspaceMemoryUnsupported),
    ("v2_still_accepted_by_v2_backend", Tests.VersionTwoCompatibility),
    ("memory_package_rejects_legacy_local_backend", Tests.MemoryUnsupported),
    ("memory_package_rejects_missing_capability", Tests.MemoryCapabilityMissing),
    ("unknown_package_version_never_imports", Tests.UnknownPackageVersion),
    ("hash_mismatch_never_imports_or_acknowledges", Tests.BadHash),
    ("import_failure_never_acknowledges", Tests.ImportFailure),
    ("ack_failure_keeps_local_success", Tests.AckFailure),
    ("cancel_after_import_keeps_local_success", Tests.AckCancellation),
    ("ack_invalid_digest_keeps_local_success", Tests.BadAcknowledgement),
    ("invalid_import_receipt_never_acknowledges", Tests.BadImportReceipt),
    ("retry_duplicate_can_finish_ack", Tests.DuplicateRetry),
    ("oversized_package_never_imports", Tests.Oversize),
    ("foreign_file_url_rejected_before_network", Tests.ForeignDownload),
    ("file_download_atomic_and_cleanup", Tests.Download),
    ("local_target_and_same_endpoint_rejected", Tests.InvalidTargets),
    ("inbox_and_status_are_parsed", Tests.Inbox),
    ("unauthorized_clears_session", Tests.Unauthorized),
    ("workspace_unauthorized_also_clears_session", Tests.WorkspaceUnauthorized),
    ("redirect_is_not_followed", Tests.Redirect),
};
var failures = 0;
foreach (var (name, run) in cases)
{
    try { await run(); Console.WriteLine("PASS " + name); }
    catch (Exception error) { failures++; Console.WriteLine("FAIL " + name + ": " + error.GetType().Name + " " + error.Message); }
}
Console.WriteLine($"{cases.Length - failures} passed, {failures} failed");
return failures == 0 ? 0 : 1;

static class Tests
{
    public static void Check(bool condition, string message = "assertion failed") { if (!condition) throw new Exception(message); }
    public static async Task<T> Throws<T>(Func<Task> run) where T : Exception
    { try { await run(); } catch (T error) { return error; } throw new Exception("Expected " + typeof(T).Name); }
    public static HttpResponseMessage Json(object body, HttpStatusCode status = HttpStatusCode.OK)
        => new(status) { Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json") };

    public static async Task InvalidCredentials()
    {
        var requests = 0;
        using var session = new BackendSession(new Uri("http://server.test/api"), new Handler((_, _) =>
        {
            requests++;
            return Task.FromResult(Json(new { }));
        }));
        foreach (var username in new[] { "", "ab", new string('a', 33), "_account", "bad name", "测试账号", "abc!" })
        {
            Check(BackendSession.ValidateCredentials(username, "long-password-123", true)?.Contains("用户名") == true);
            await Throws<ArgumentException>(() => session.RegisterAsync(username, "long-password-123"));
            await Throws<ArgumentException>(() => session.LoginAsync(username, "123"));
        }
        foreach (var password in new[] { "", "123", new string('p', 129), new string(' ', 12) })
        {
            Check(BackendSession.ValidateCredentials("account", password, true)?.Contains("密码") == true);
            await Throws<ArgumentException>(() => session.RegisterAsync("account", password));
        }
        await Throws<ArgumentException>(() => session.LoginAsync("account", ""));
        await Throws<ArgumentException>(() => session.LoginAsync("account", new string('p', 129)));
        Check(requests == 0, "Invalid input reached the backend");
    }
    public static Task CredentialBoundaries()
    {
        foreach (var username in new[] { "abc", " A.B_c-1 ", new string('a', 32) })
        {
            Check(BackendSession.ValidateCredentials(username, new string('p', 12), true) == null);
            Check(BackendSession.ValidateCredentials(username, new string('p', 128), true) == null);
            Check(BackendSession.ValidateCredentials(username, "1", false) == null);
        }
        var unicodePassword = string.Concat(Enumerable.Repeat("😀", 128));
        Check(BackendSession.ValidateCredentials("account", unicodePassword, true) == null);
        Check(BackendSession.ValidateCredentials("account", unicodePassword + "😀", true) != null);
        Check(BackendSession.ValidateCredentials("account", " ", false) == null, "Legacy whitespace passwords must not be altered");
        return Task.CompletedTask;
    }
    public static async Task LegacyLogin()
    {
        var requests = 0;
        using var session = new BackendSession(new Uri("http://server.test/api"), new Handler(async (request, ct) =>
        {
            requests++;
            Check(request.RequestUri!.AbsolutePath == "/api/auth/login");
            using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct));
            Check(body.RootElement.GetProperty("username").GetString() == "Old_User");
            Check(body.RootElement.GetProperty("password").GetString() == " 1 ", "Password was trimmed or rejected");
            return Json(new { access_token = Fixture.ServerToken, user = new { id = Guid.NewGuid(), username = "old_user", role = "user" } });
        }));
        await session.LoginAsync(" Old_User ", " 1 ");
        Check(requests == 1 && session.IsAuthenticated);
    }
    public static async Task ValidationArray()
    {
        const string secret = "never-echo-password-7391";
        using var session = new BackendSession(new Uri("http://server.test/api"), new Handler((_, _) => Task.FromResult(Json(new
        {
            detail = new object[]
            {
                new { loc = new[] { "body", "username" }, type = "string_pattern_mismatch", msg = "Untrusted " + secret, input = secret },
                new { loc = new[] { "body", "password" }, type = "too_short", msg = "Untrusted " + secret, input = secret, ctx = new { min_length = secret } },
                new { loc = new[] { "body", "display_name" }, type = "string_too_long", msg = "Untrusted " + secret },
                new { loc = new[] { "body", secret }, type = secret, msg = secret, input = secret },
            }
        }, HttpStatusCode.UnprocessableEntity))));
        var error = await Throws<BackendRequestException>(() => session.RegisterAsync("valid_user", "valid-password-123"));
        Check(error.StatusCode == HttpStatusCode.UnprocessableEntity);
        Check(error.Message.Contains("用户名须以英文字母或数字开头"));
        Check(error.Message.Contains("注册密码长度应为 12～128"));
        Check(error.Message.Contains("显示名称最多允许 64"));
        Check(error.Message.Contains("请求参数不符合要求"));
        Check(!error.Message.Contains(secret) && !error.Message.Contains("Untrusted"), "Validation error echoed a secret");
    }
    public static async Task ValidationArrayFallback()
    {
        using var session = new BackendSession(new Uri("http://server.test/api"), new Handler((_, _) => Task.FromResult(Json(new
        {
            detail = new object[]
            {
                new { loc = new[] { "body", "username" }, type = "missing" },
                new { loc = new[] { "body", "password" }, type = "string_too_long" },
                new { type = "too_short", input = "never-print-this-input" },
                42,
            }
        }, HttpStatusCode.UnprocessableEntity))));
        var error = await Throws<BackendRequestException>(() => session.LoginAsync("account", "123"));
        Check(error.Message.Contains("请填写用户名") && error.Message.Contains("密码最多允许 128"));
        Check(error.Message.Contains("请求参数长度不足") && error.Message.Contains("请求参数格式不正确"));
        Check(!error.Message.Contains("never-print-this-input"));
    }
    public static async Task StringError()
    {
        using var session = new BackendSession(new Uri("http://server.test/api"), new Handler((_, _) =>
            Task.FromResult(Json(new { detail = "用户名已存在" }, HttpStatusCode.Conflict))));
        var error = await Throws<BackendRequestException>(() => session.RegisterAsync("account", "valid-password-123"));
        Check(error.Message == "用户名已存在" && error.StatusCode == HttpStatusCode.Conflict);
    }

    public static async Task SeparateSessions()
    {
        using var setup = new Fixture(); await setup.Login();
        Check(setup.Server.UserId == setup.ServerUser && setup.Local.UserId == setup.LocalUser);
        Check(setup.Server.Client.DefaultRequestHeaders.Authorization?.Parameter == Fixture.ServerToken);
        Check(setup.Local.Client.DefaultRequestHeaders.Authorization?.Parameter == Fixture.LocalToken);
        Check(setup.Server.Role == "user");
        await setup.Local.LogoutAsync();
        Check(!setup.Local.IsAuthenticated && setup.Server.IsAuthenticated);
    }
    public static async Task Success()
    {
        using var f = new Fixture(); await f.Login();
        var receipt = await f.Transfer.ReceiveAsync(f.Delivery);
        Check(receipt.Acknowledged && receipt.LocalProjectId == f.ProjectId && !receipt.Duplicate);
        Check(f.Imports == 1 && f.Acks == 1);
        Check(f.Events.SequenceEqual(new[] { "download", "import", "ack" }));
        using var body = JsonDocument.Parse(f.ImportedBody!);
        Check(body.RootElement.GetProperty("package").GetRawText() == Encoding.UTF8.GetString(f.Package));
    }
    public static async Task BadHash()
    {
        using var f = new Fixture { CorruptHash = true }; await f.Login();
        await Throws<InvalidDataException>(() => f.Transfer.ReceiveAsync(f.Delivery));
        Check(f.Imports == 0 && f.Acks == 0);
    }
    public static async Task MemoryPackage()
    {
        using var f = new Fixture();
        f.SetPackage("{\"schema_version\":2,\"memory\":{\"run_links\":[{\"baseline_run_id\":\"old-run\"}],\"revisions\":[{\"snapshot\":\"历史问题与完整中文意见\"}]},\"large\":9007199254740993}");
        await f.Login();
        var receipt = await f.Transfer.ReceiveAsync(f.Delivery);
        Check(receipt.Acknowledged && f.CapabilityChecks == 1 && f.Imports == 1 && f.Acks == 1);
        using var body = JsonDocument.Parse(f.ImportedBody!);
        Check(body.RootElement.GetProperty("package").GetRawText() == Encoding.UTF8.GetString(f.Package));
    }
    public static async Task WorkspaceMemoryPackage()
    {
        using var f = new Fixture();
        f.SetPackage("{\"schema_version\":3,\"memory\":{\"revisions\":[{\"evidence\":\"原文中文证据\"}],\"profile\":{\"facts\":[{\"key\":\"范围\",\"value\":\"仅本项目\"}]},\"feedback\":[{\"action\":\"resolved\",\"evidence\":\"已补充清单\"}]},\"large\":9007199254740993}");
        await f.Login();
        var receipt = await f.Transfer.ReceiveAsync(f.Delivery);
        Check(receipt.Acknowledged && f.CapabilityChecks == 1 && f.Imports == 1 && f.Acks == 1);
        using var body = JsonDocument.Parse(f.ImportedBody!);
        Check(body.RootElement.GetProperty("package").GetRawText() == Encoding.UTF8.GetString(f.Package), "v3 facts/feedback were changed or dropped");
    }
    public static async Task ActualWorkspaceMemoryFixture()
    {
        using var f = new Fixture();
        var json = await File.ReadAllTextAsync(Path.Combine(AppContext.BaseDirectory, "Fixtures", "memory-v3.json"));
        using var actual = JsonDocument.Parse(json);
        Check(actual.RootElement.GetProperty("schema_version").GetInt32() == 3);
        Check(actual.RootElement.GetProperty("memory").GetProperty("profile").GetProperty("facts").GetArrayLength() > 0);
        Check(actual.RootElement.GetProperty("memory").GetProperty("feedback").GetArrayLength() == 2);
        f.SetPackage(json);
        await f.Login();
        Check((await f.Transfer.ReceiveAsync(f.Delivery)).Acknowledged);
        using var body = JsonDocument.Parse(f.ImportedBody!);
        Check(body.RootElement.GetProperty("package").GetRawText() == json, "Actual v3 profile/feedback/attachment bytes changed");
    }
    public static async Task WorkspaceMemoryUnsupported()
    {
        using var f = new Fixture { MaximumPackageVersion = 2 };
        f.SetPackage("{\"schema_version\":3,\"memory\":{\"feedback\":[{\"action\":\"needs_review\"}]}}");
        await f.Login();
        await Throws<InvalidDataException>(() => f.Transfer.ReceiveAsync(f.Delivery));
        Check(f.CapabilityChecks == 1 && f.Imports == 0 && f.Acks == 0, "v3 was silently downgraded into v2");
    }
    public static async Task VersionTwoCompatibility()
    {
        using var f = new Fixture { MaximumPackageVersion = 2 };
        f.SetPackage("{\"schema_version\":2,\"memory\":{\"revisions\":[]}}");
        await f.Login();
        Check((await f.Transfer.ReceiveAsync(f.Delivery)).Acknowledged);
    }
    public static async Task WorkspaceUnauthorized()
    {
        using var session = new BackendSession(new Uri("http://server.test/api"), new Handler((request, _) =>
            Task.FromResult(request.RequestUri!.AbsolutePath.EndsWith("/auth/login")
                ? Json(new { access_token = Fixture.ServerToken, user = new { id = Guid.NewGuid(), username = "account", role = "admin" } })
                : Json(new { detail = "expired" }, HttpStatusCode.Unauthorized))));
        await session.LoginAsync("account", "password");
        using var response = await session.Client.GetAsync(session.ResolveApiUri("evaluate/results"));
        Check(response.StatusCode == HttpStatusCode.Unauthorized && !session.IsAuthenticated);
    }
    public static async Task MemoryUnsupported()
    {
        using var f = new Fixture { CapabilitiesStatus = HttpStatusCode.NotFound };
        f.SetPackage("{\"schema_version\":2,\"memory\":{}}");
        await f.Login();
        var error = await Throws<InvalidDataException>(() => f.Transfer.ReceiveAsync(f.Delivery));
        Check(error.Message.Contains("问题记忆") && f.Imports == 0 && f.Acks == 0);
    }
    public static async Task MemoryCapabilityMissing()
    {
        using var f = new Fixture { SupportsMemory = false };
        f.SetPackage("{\"schema_version\":2,\"memory\":{}}");
        await f.Login();
        await Throws<InvalidDataException>(() => f.Transfer.ReceiveAsync(f.Delivery));
        Check(f.Imports == 0 && f.Acks == 0);
    }
    public static async Task UnknownPackageVersion()
    {
        using var f = new Fixture();
        f.SetPackage("{\"schema_version\":99}");
        await f.Login();
        await Throws<InvalidDataException>(() => f.Transfer.ReceiveAsync(f.Delivery));
        Check(f.Imports == 0 && f.Acks == 0);
    }
    public static async Task ImportFailure()
    {
        using var f = new Fixture { ImportStatus = HttpStatusCode.UnprocessableEntity }; await f.Login();
        await Throws<BackendRequestException>(() => f.Transfer.ReceiveAsync(f.Delivery)); Check(f.Acks == 0);
    }
    public static async Task AckFailure()
    {
        using var f = new Fixture { AckStatus = HttpStatusCode.ServiceUnavailable }; await f.Login();
        var result = await f.Transfer.ReceiveAsync(f.Delivery);
        Check(!result.Acknowledged && result.LocalProjectId == f.ProjectId && result.Message.Contains("已保存"));
    }
    public static async Task AckCancellation()
    {
        using var f = new Fixture(); await f.Login();
        using var cancellation = new CancellationTokenSource(); f.AfterImport = cancellation.Cancel;
        var result = await f.Transfer.ReceiveAsync(f.Delivery, cancellation.Token);
        Check(!result.Acknowledged && result.LocalProjectId == f.ProjectId);
    }
    public static async Task BadAcknowledgement()
    {
        using var f = new Fixture { BadAckDigest = true }; await f.Login();
        Check(!(await f.Transfer.ReceiveAsync(f.Delivery)).Acknowledged);
    }
    public static async Task BadImportReceipt()
    {
        using var f = new Fixture { BadImportDigest = true }; await f.Login();
        await Throws<InvalidDataException>(() => f.Transfer.ReceiveAsync(f.Delivery)); Check(f.Acks == 0);
    }
    public static async Task DuplicateRetry()
    {
        using var f = new Fixture { Duplicate = true }; await f.Login();
        var result = await f.Transfer.ReceiveAsync(f.Delivery);
        Check(result.Duplicate && result.Acknowledged);
    }
    public static async Task Oversize()
    {
        using var f = new Fixture { Oversized = true }; await f.Login();
        await Throws<InvalidDataException>(() => f.Transfer.ReceiveAsync(f.Delivery)); Check(f.Imports == 0);
    }
    public static async Task ForeignDownload()
    {
        using var f = new Fixture(); await f.Login();
        foreach (var address in new[] { "https://untrusted.test/api/file", "//untrusted.test/api/file", "/api/../outside", "http://name:secret@127.0.0.1:18082/api/file" })
            await Throws<InvalidOperationException>(() => f.Local.DownloadFileAsync(address, Path.Combine(Path.GetTempPath(), "never-written")));
        Check(f.Files == 0);
    }
    public static async Task Download()
    {
        using var f = new Fixture(); await f.Login();
        var directory = Path.Combine(Path.GetTempPath(), "aiassist-download-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory); var target = Path.Combine(directory, "report.txt");
        try
        {
            await File.WriteAllTextAsync(target, "previous");
            await f.Local.DownloadFileAsync("/api/file", target);
            Check(await File.ReadAllTextAsync(target) == "原始附件");
            f.FileError = true;
            await Throws<IOException>(() => f.Local.DownloadFileAsync("/api/file", target));
            Check(await File.ReadAllTextAsync(target) == "原始附件");
            Check(Directory.GetFiles(directory).Length == 1);
        }
        finally { File.Delete(target); Directory.Delete(directory); }
    }
    public static async Task InvalidTargets()
    {
        using var remote = new BackendSession(new Uri("https://remote.test/api"));
        using var remote2 = new BackendSession(new Uri("https://another.test/api"));
        using var local = new BackendSession(new Uri("http://127.0.0.1:8000/api"));
        await Throws<ArgumentException>(() => { _ = new HistoryTransferClient(remote, remote2); return Task.CompletedTask; });
        await Throws<ArgumentException>(() => { _ = new HistoryTransferClient(local, local); return Task.CompletedTask; });
    }
    public static async Task Inbox()
    {
        using var f = new Fixture(); await f.Login();
        var items = await f.Transfer.ListInboxAsync(); Check(items.Count == 1 && items[0].Id == f.Delivery.Id);
    }
    public static async Task Unauthorized()
    {
        using var f = new Fixture { DownloadStatus = HttpStatusCode.Unauthorized }; await f.Login();
        await Throws<BackendRequestException>(() => f.Transfer.ReceiveAsync(f.Delivery));
        Check(!f.Server.IsAuthenticated && f.Local.IsAuthenticated);
    }
    public static async Task Redirect()
    {
        using var f = new Fixture { DownloadStatus = HttpStatusCode.Redirect }; await f.Login();
        await Throws<BackendRequestException>(() => f.Transfer.ReceiveAsync(f.Delivery)); Check(f.Imports == 0);
    }
}

sealed class Fixture : IDisposable
{
    public const string ServerToken = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    public const string LocalToken = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    public string ServerUser = Guid.NewGuid().ToString(), LocalUser = Guid.NewGuid().ToString(), ProjectId = Guid.NewGuid().ToString();
    public BackendSession Server, Local;
    public HistoryTransferClient Transfer;
    public byte[] Package = Encoding.UTF8.GetBytes("{\"schema_version\":1,\"text\":\"原始报告\",\"large\":9007199254740993,\"decimal\":1.234567890123456789e+19}");
    public HistoryDelivery Delivery;
    public int Imports, Acks, Files;
    public int CapabilityChecks;
    public bool SupportsMemory = true;
    public int MaximumPackageVersion = 3;
    public HttpStatusCode CapabilitiesStatus = HttpStatusCode.OK;
    public bool CorruptHash, BadImportDigest, BadAckDigest, Duplicate, Oversized, FileError;
    public HttpStatusCode ImportStatus = HttpStatusCode.OK, AckStatus = HttpStatusCode.OK, DownloadStatus = HttpStatusCode.OK;
    public byte[]? ImportedBody;
    public List<string> Events = [];
    public Action? AfterImport;
    public Fixture()
    {
        Delivery = new(Guid.NewGuid().ToString(), Convert.ToHexStringLower(SHA256.HashData(Package)), "pending", Guid.NewGuid().ToString(), DateTimeOffset.UtcNow);
        Server = new(new Uri("http://server.test:8000/api"), new Handler((r, ct) => Handle(r, true, ct)));
        Local = new(new Uri("http://127.0.0.1:18082/api"), new Handler((r, ct) => Handle(r, false, ct)));
        Transfer = new(Server, Local);
    }
    public async Task Login() { await Server.LoginAsync("server_user", "server_password"); await Local.LoginAsync("local_user", "local_password"); }
    public void SetPackage(string json)
    {
        Package = Encoding.UTF8.GetBytes(json);
        Delivery = Delivery with { PackageSha256 = Convert.ToHexStringLower(SHA256.HashData(Package)) };
    }
    private async Task<HttpResponseMessage> Handle(HttpRequestMessage request, bool server, CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        var path = request.RequestUri!.AbsolutePath;
        if (path.EndsWith("/auth/login"))
        {
            Tests.Check(request.Headers.Authorization == null);
            return Tests.Json(new { access_token = server ? ServerToken : LocalToken, user = new { id = server ? ServerUser : LocalUser, username = server ? "server_user" : "local_user", role = "user" } });
        }
        Tests.Check(request.Headers.Authorization?.Parameter == (server ? ServerToken : LocalToken), "token leaked between backends");
        if (path.EndsWith("/auth/logout")) return new(HttpStatusCode.NoContent);
        if (path.EndsWith("/history/capabilities"))
        {
            Tests.Check(!server);
            CapabilityChecks++;
            return Tests.Json(new { package_versions = SupportsMemory ? Enumerable.Range(1, MaximumPackageVersion).ToArray() : new[] { 1 }, issue_memory = SupportsMemory }, CapabilitiesStatus);
        }
        if (path.EndsWith("/history/inbox")) return Tests.Json(new { items = new[] { new { id = Delivery.Id, package_sha256 = Delivery.PackageSha256, status = "pending", project_id = Delivery.ProjectId, created_at = Delivery.CreatedAt } } });
        if (path.EndsWith("/package"))
        {
            Events.Add("download");
            if (DownloadStatus != HttpStatusCode.OK) return Tests.Json(new { detail = "unavailable" }, DownloadStatus);
            var response = new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(Package) };
            response.Headers.Add("X-Package-SHA256", CorruptHash ? new string('0', 64) : Delivery.PackageSha256);
            if (Oversized) response.Content.Headers.ContentLength = 49L * 1024 * 1024;
            return response;
        }
        if (path.EndsWith("/history/imports"))
        {
            Events.Add("import"); Imports++;
            ImportedBody = await request.Content!.ReadAsByteArrayAsync(ct);
            if (ImportStatus != HttpStatusCode.OK) return Tests.Json(new { detail = "Import rejected" }, ImportStatus);
            // Cancellation is triggered by the ACK handler, once the local receipt was already read.
            return Tests.Json(new { project_id = ProjectId, delivery_id = Delivery.Id, package_sha256 = BadImportDigest ? new string('0', 64) : Delivery.PackageSha256, duplicate = Duplicate });
        }
        if (path.EndsWith("/ack"))
        {
            Events.Add("ack"); Acks++; AfterImport?.Invoke(); ct.ThrowIfCancellationRequested();
            if (AckStatus != HttpStatusCode.OK) return Tests.Json(new { detail = "Ack unavailable" }, AckStatus);
            return Tests.Json(new { id = Delivery.Id, package_sha256 = BadAckDigest ? new string('0', 64) : Delivery.PackageSha256, status = "received" });
        }
        if (path == "/api/file")
        {
            Files++;
            return new(HttpStatusCode.OK) { Content = FileError ? new StreamContent(new BrokenStream()) : new StringContent("原始附件", Encoding.UTF8) };
        }
        throw new Exception("Unexpected route " + path);
    }
    public void Dispose() { Server.Dispose(); Local.Dispose(); }
}

sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> callback) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => callback(request, cancellationToken);
}

sealed class BrokenStream : MemoryStream
{
    private int reads;
    public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
    {
        if (++reads == 1) { buffer.Span[0] = 65; return ValueTask.FromResult(1); }
        throw new IOException("Test interrupted stream");
    }
}
