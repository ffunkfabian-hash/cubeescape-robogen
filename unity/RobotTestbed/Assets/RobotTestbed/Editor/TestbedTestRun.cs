using System;
using System.Collections.Generic;
using System.IO;
using System.Security;
using System.Text;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;

namespace RobotTestbed.Editor
{
    /// <summary>Runs the EditMode tests inside the pipeline's Unity process (synchronously) and writes tests.xml in
    /// the shape run_testbed.sh reads. Before 1.13 the tests needed a second Unity start (about 10 s of the run).</summary>
    public sealed class TestbedTestRun : ICallbacks
    {
        readonly List<(string name, string result, string message)> cases = new List<(string, string, string)>();
        double seconds;

        public static (int passed, int failed) Run(string reportDir)
        {
            var api = ScriptableObject.CreateInstance<TestRunnerApi>();
            var run = new TestbedTestRun();
            api.RegisterCallbacks(run);
            try
            {
                var filter = new Filter { testMode = TestMode.EditMode, assemblyNames = new[] { "RobotTestbed.Tests" } };
                api.Execute(new ExecutionSettings(filter) { runSynchronously = true });
            }
            finally
            {
                api.UnregisterCallbacks(run);
                UnityEngine.Object.DestroyImmediate(api);
            }
            return run.Write(Path.Combine(reportDir, "tests.xml"));
        }

        (int, int) Write(string path)
        {
            int passed = 0, failed = 0;
            var body = new StringBuilder();
            foreach (var (name, result, message) in cases)
            {
                if (result == "Passed") passed++; else failed++;
                body.Append("  <test-case fullname=\"").Append(SecurityElement.Escape(name)).Append("\" result=\"")
                    .Append(result).Append("\">");
                if (result != "Passed")
                    body.Append("<failure><message>").Append(SecurityElement.Escape(message ?? "")).Append("</message></failure>");
                body.Append("</test-case>\n");
            }
            File.WriteAllText(path, "<test-run total=\"" + cases.Count + "\" passed=\"" + passed + "\" failed=\"" + failed +
                                    "\" duration=\"" + seconds.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture) +
                                    "\">\n" + body + "</test-run>\n");
            return (passed, failed);
        }

        public void RunStarted(ITestAdaptor testsToRun) { }

        public void RunFinished(ITestResultAdaptor result) => seconds = result.Duration;

        public void TestStarted(ITestAdaptor test) { }

        public void TestFinished(ITestResultAdaptor result)
        {
            if (result.HasChildren) return;          // only leaves: the test methods
            cases.Add((result.FullName, result.TestStatus.ToString(), result.Message));
        }
    }
}
