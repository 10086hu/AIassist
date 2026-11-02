using AiReportDesktop;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

var cases = new (string Name, Func<Task> Run)[]
{
    ("real_session_tokens_are_separate", Tests.SeparateSessions),
    ("receive_keeps_utf8_and_number_tokens", Tests.Success),
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
    public static async Task Throws<T>(Func<Task> run) where T : Exception
    { try { await run(); } catch (T) { return; } throw new Exception("Expected " + typeof(T).Name); }
    public static HttpResponseMessage Json(object body, HttpStatusCode status = HttpStatusCode.OK)
        => new(status) { Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json") };

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
