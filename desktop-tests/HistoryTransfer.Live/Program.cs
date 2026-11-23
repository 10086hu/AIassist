using AiReportDesktop;
using System.Diagnostics;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

var options = new Dictionary<string, string>();
for (var i = 0; i < args.Length; i += 2)
{
    if (i + 1 >= args.Length || !args[i].StartsWith("--")) throw new ArgumentException("Expected --option value pairs");
    options.Add(args[i], args[i + 1]);
}
string Option(string name) => options.TryGetValue(name, out var value) ? value : throw new ArgumentException("Missing " + name);
var reportPath = Path.GetFullPath(Option("--report"));
var reportDirectory = Path.GetDirectoryName(reportPath)!;
Directory.CreateDirectory(reportDirectory);
var run = Guid.NewGuid().ToString("N");
var marker = "desktop-live-" + run;
var serverUsername = "hist_live_s_" + run[..12];
var localUsername = "hist_live_l_" + run[..12];
string Password() => Convert.ToBase64String(RandomNumberGenerator.GetBytes(30));
var serverPassword = Password();
var localPassword = Password();
using var server = new BackendSession(new Uri(Option("--server-url")));
using var local = new BackendSession(new Uri(Option("--local-url")));
using var administrator = new BackendSession(server.ApiBase);
var checks = new JsonObject();
var report = new JsonObject
{
    ["run_id"] = run, ["marker"] = marker, ["server_url"] = server.ApiBase.ToString(),
    ["local_url"] = local.ApiBase.ToString(), ["status"] = "in_progress", ["checks"] = checks,
};
async Task Save() => await File.WriteAllTextAsync(reportPath, report.ToJsonString(new JsonSerializerOptions { WriteIndented = true }), Encoding.UTF8);
void Check(bool condition, string label) { if (!condition) throw new InvalidOperationException(label); }
async Task<JsonDocument> JsonResponse(HttpResponseMessage response, string operation)
{
    using (response)
    {
        Check(response.IsSuccessStatusCode, operation + " HTTP " + (int)response.StatusCode);
        return JsonDocument.Parse(await response.Content.ReadAsByteArrayAsync());
    }
}
async Task<JsonDocument> Get(BackendSession session, string path, string label)
    => await JsonResponse(await session.Client.GetAsync(session.ResolveApiUri(path)), label);
async Task<JsonDocument> Post(BackendSession session, string path, object value, string label)
    => await JsonResponse(await session.Client.PostAsJsonAsync(session.ResolveApiUri(path), value), label);
var exitCode = 1;
try
{
    var privateDirectory = Path.GetFullPath(Path.Combine(".cache", "history-live-20261102"));
    Directory.CreateDirectory(privateDirectory);
    if (OperatingSystem.IsWindows())
    {
        var process = new ProcessStartInfo("icacls") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        process.ArgumentList.Add(privateDirectory);
        process.ArgumentList.Add("/inheritance:r");
        process.ArgumentList.Add("/grant:r");
        process.ArgumentList.Add(Environment.UserDomainName + "\\" + Environment.UserName + ":(OI)(CI)F");
        using var acl = Process.Start(process)!;
        await acl.WaitForExitAsync();
        Check(acl.ExitCode == 0, "private_test_credentials_acl_failed");
    }
    var privatePath = Path.Combine(privateDirectory, "credentials-" + run + ".json");
    await File.WriteAllTextAsync(privatePath, JsonSerializer.Serialize(new
    {
        server_url = server.ApiBase.ToString(), server_username = serverUsername, server_password = serverPassword,
        local_url = local.ApiBase.ToString(), local_username = localUsername, local_password = localPassword,
    }), Encoding.UTF8);
    report["private_test_credentials_path"] = privatePath;
    await Save();

    using var administratorCredentials = JsonDocument.Parse(await File.ReadAllTextAsync(Option("--admin-credentials"), Encoding.UTF8));
    await administrator.LoginAsync(administratorCredentials.RootElement.GetProperty("username").GetString()!, administratorCredentials.RootElement.GetProperty("password").GetString()!);
    Check(administrator.Role == "admin", "administrator_role_required");
    await server.RegisterAsync(serverUsername, serverPassword, "Desktop receive verification");
    await server.LoginAsync(serverUsername, serverPassword);
    report["server_user_id"] = server.UserId; report["server_username"] = serverUsername; await Save();
    await local.RegisterAsync(localUsername, localPassword, "Desktop local receive verification");
    await local.LoginAsync(localUsername, localPassword);
    report["local_user_id"] = local.UserId; report["local_username"] = localUsername; await Save();
    Check(server.Role == "user" && local.Role == "user", "ordinary_user_required");
    checks["independent_real_logins"] = "passed";

    var package = JsonNode.Parse(await File.ReadAllTextAsync(Option("--package-file"), Encoding.UTF8))!;
    package["project"]!["name"] = marker;
    package["project"]!["description"] = "Synthetic end-to-end desktop receive verification";
    var originalSource = package["sources"]!.AsArray().First()!;
    var sourceHash = originalSource["sha256"]!.GetValue<string>();
    var clientRecordId = Guid.NewGuid().ToString();
    report["client_record_id"] = clientRecordId;
    using var uploaded = await Post(server, "history/uploads", new { client_record_id = clientRecordId, package }, "upload");
    var uploadId = uploaded.RootElement.GetProperty("id").GetString()!;
    var serverProjectId = uploaded.RootElement.GetProperty("project_id").GetString()!;
    report["server_upload_id"] = uploadId; report["server_project_id"] = serverProjectId; await Save();
    using var dispatched = await Post(administrator, "admin/history/deliveries",
        new { recipient_user_id = server.UserId, project_id = serverProjectId }, "dispatch");
    var deliveryId = dispatched.RootElement.GetProperty("id").GetString()!;
    report["server_delivery_id"] = deliveryId; await Save();
    var transfer = new HistoryTransferClient(server, local);
    var inbox = await transfer.ListInboxAsync();
    var delivery = inbox.Single(row => row.Id == deliveryId);
    var result = await transfer.ReceiveAsync(delivery);
    Check(result.Acknowledged && !result.Duplicate, "initial_receive_acknowledgement");
    report["local_project_id"] = result.LocalProjectId; checks["server_package_received_by_csharp"] = "passed"; await Save();
    var retry = await transfer.ReceiveAsync(delivery);
    Check(retry.Acknowledged && retry.Duplicate && retry.LocalProjectId == result.LocalProjectId, "receive_retry_deduplication");
    checks["receive_retry_deduplication"] = "passed";

    using var remotePackage = await Get(server, "history/inbox/" + deliveryId + "/package", "read_received_package");
    using var receipt = await Post(local, "history/imports", new { delivery_id = deliveryId, package_sha256 = delivery.PackageSha256, package = remotePackage.RootElement }, "inspect_import_receipt");
    report["local_import_id"] = receipt.RootElement.GetProperty("id").GetString(); await Save();
    using var tree = await Get(local, "projects/tree", "local_record_tree");
    var localProject = tree.RootElement.GetProperty("items").EnumerateArray().Single(item => item.GetProperty("id").GetString() == result.LocalProjectId);
    Check(localProject.GetProperty("name").GetString() == marker, "local_project_marker");
    var resultId = localProject.GetProperty("check_runs")[0].GetProperty("results")[0].GetProperty("id").GetString()!;
    report["local_result_id"] = resultId;
    using var detail = await Get(local, "evaluate/results/" + resultId, "local_result_detail");
    var source = detail.RootElement.GetProperty("source_documents")[0];
    report["local_source_id"] = source.GetProperty("id").GetString();
    var previewPath = source.GetProperty("preview_url").GetString()!;
    using var preview = await local.Client.GetAsync(local.ResolveApiUri(previewPath));
    Check(preview.IsSuccessStatusCode, "local_preview_failed");
    var html = await preview.Content.ReadAsStringAsync();
    Check(html.Contains("历史记录服务器验证样本"), "local_preview_original_text_missing");
    checks["local_history_and_preview"] = "passed";
    var target = Path.Combine(reportDirectory, "received-" + run + ".txt");
    await local.DownloadFileAsync(source.GetProperty("download_url").GetString()!, target);
    Check(Convert.ToHexStringLower(SHA256.HashData(await File.ReadAllBytesAsync(target))) == sourceHash, "saved_original_file_hash_mismatch");
    report["downloaded_file"] = target; report["source_sha256"] = sourceHash;
    checks["original_file_saved_and_verified"] = "passed";
    var received = (await transfer.ListInboxAsync(status: "received")).Single(item => item.Id == deliveryId);
    Check(received.Status == "received", "server_ack_state_missing");
    checks["server_received_state"] = "passed";
    report["status"] = "passed";
    report["cleanup"] = "Retained for UI verification; remove only IDs listed in this report afterwards.";
    exitCode = 0;
}
catch (Exception error)
{
    report["status"] = "failed";
    report["error_type"] = error.GetType().Name;
    // Fixed diagnostic labels only; never print request bodies, tokens, or credential documents.
    if (error is InvalidOperationException && !error.Message.Contains("\n")) report["error"] = error.Message;
}
finally
{
    foreach (var session in new[] { administrator, server, local })
    {
        try { await session.LogoutAsync(); } catch { session.ClearAuthentication(); }
    }
    report["completed_at"] = DateTimeOffset.Now.ToString("O");
    await Save();
}
Console.WriteLine(JsonSerializer.Serialize(new { status = report["status"]?.GetValue<string>(), report = reportPath }));
return exitCode;
