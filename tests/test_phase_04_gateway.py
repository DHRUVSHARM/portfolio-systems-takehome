from contextlib import asynccontextmanager
import threading
import time
import unittest

import httpx

from services.gateway import GatewayConfig, create_app
from services.gateway.client import (
    DownstreamResult,
    DownstreamTimeoutError,
    PortfolioApiClient,
)


BUSINESS_RESPONSE = {
    "holdings": {"AAPL": 1.0},
    "lookback_days": 30,
    "metrics": {"AAPL": {"ticker": "AAPL"}},
    "risk": {"portfolio_sharpe": 1.0},
    "summary": "Gateway summary.",
}


class FakePortfolioClient:
    def __init__(
        self,
        *,
        delay_seconds=0.0,
        status_code=200,
        body=None,
        failure=None,
    ):
        self.delay_seconds = delay_seconds
        self.status_code = status_code
        self.body = body or BUSINESS_RESPONSE
        self.failure = failure
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.closed = False

    async def analyze(self, *, payload, headers):
        self.calls.append({"payload": payload, "headers": headers})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay_seconds:
                await anyio_sleep(self.delay_seconds)
            if self.failure is not None:
                raise self.failure
            return DownstreamResult(
                status_code=self.status_code,
                body=self.body,
                headers={
                    name: value
                    for name, value in headers.items()
                    if name in {"X-Run-ID", "X-Request-ID", "X-Query-ID"}
                },
            )
        finally:
            self.active -= 1

    async def aclose(self):
        self.closed = True


async def anyio_sleep(seconds):
    import asyncio

    await asyncio.sleep(seconds)


class Phase4GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_downstream_calls_never_exceed_max_in_flight(self):
        downstream = FakePortfolioClient(delay_seconds=0.03)
        app = _app_for(
            downstream,
            GatewayConfig(max_in_flight=2, queue_capacity=8, queue_timeout_seconds=1),
        )

        async with _client_for_app(app) as client:
            responses = await _gather_posts(client, 8)

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertLessEqual(downstream.max_active, 2)
        self.assertEqual(len(downstream.calls), 8)

    async def test_waiting_never_exceeds_queue_capacity_and_extra_gets_503(self):
        downstream = FakePortfolioClient(delay_seconds=0.08)
        app = _app_for(
            downstream,
            GatewayConfig(max_in_flight=1, queue_capacity=2, queue_timeout_seconds=1),
        )

        async with _client_for_app(app) as client:
            responses = await _gather_posts(client, 4)

        statuses = sorted(response.status_code for response in responses)
        self.assertEqual(statuses, [200, 200, 200, 503])
        snapshot = await app.state.admission.snapshot()
        self.assertEqual(snapshot.waiting, 0)
        self.assertEqual(snapshot.active, 0)
        self.assertEqual(len(downstream.calls), 3)

    async def test_request_beyond_active_and_queue_immediate_503(self):
        downstream = FakePortfolioClient(delay_seconds=0.2)
        app = _app_for(
            downstream,
            GatewayConfig(max_in_flight=1, queue_capacity=0, queue_timeout_seconds=1),
        )

        async with _client_for_app(app) as client:
            first = _post_task(client)
            await _wait_for_call_count(downstream, 1)
            started = time.perf_counter()
            second = await _post(client)
            second_elapsed = time.perf_counter() - started
            first_response = await first

        self.assertEqual(second.status_code, 503)
        self.assertLess(second_elapsed, 0.1)
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(len(downstream.calls), 1)

    async def test_queued_request_runs_after_active_capacity_frees(self):
        downstream = FakePortfolioClient(delay_seconds=0.03)
        app = _app_for(
            downstream,
            GatewayConfig(max_in_flight=1, queue_capacity=1, queue_timeout_seconds=1),
        )

        async with _client_for_app(app) as client:
            first = _post_task(client, ticker="AAPL")
            await _wait_for_call_count(downstream, 1)
            second = _post_task(client, ticker="MSFT")
            responses = [await first, await second]

        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(
            [call["payload"]["holdings"] for call in downstream.calls],
            [{"AAPL": 1.0}, {"MSFT": 1.0}],
        )

    async def test_queued_cancellation_never_later_executes_downstream(self):
        downstream = FakePortfolioClient(delay_seconds=0.08)
        app = _app_for(
            downstream,
            GatewayConfig(max_in_flight=1, queue_capacity=1, queue_timeout_seconds=1),
        )

        async with _client_for_app(app) as client:
            active = _post_task(client, ticker="AAPL")
            await _wait_for_call_count(downstream, 1)
            queued = _post_task(client, ticker="MSFT")
            await _wait_for_waiting(app, 1)
            queued.cancel()
            await _ignore_cancelled(queued)
            active_response = await active
            await anyio_sleep(0.05)

        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(len(downstream.calls), 1)
        snapshot = await app.state.admission.snapshot()
        self.assertEqual(snapshot.active, 0)
        self.assertEqual(snapshot.waiting, 0)

    async def test_queue_timeout_releases_waiting_capacity(self):
        downstream = FakePortfolioClient(delay_seconds=0.08)
        app = _app_for(
            downstream,
            GatewayConfig(
                max_in_flight=1,
                queue_capacity=1,
                queue_timeout_seconds=0.01,
            ),
        )

        async with _client_for_app(app) as client:
            active = _post_task(client)
            await _wait_for_call_count(downstream, 1)
            timed_out = await _post(client)
            active_response = await active

        self.assertEqual(timed_out.status_code, 503)
        self.assertEqual(timed_out.json()["detail"], "gateway admission queue timed out")
        self.assertEqual(active_response.status_code, 200)
        snapshot = await app.state.admission.snapshot()
        self.assertEqual(snapshot.active, 0)
        self.assertEqual(snapshot.waiting, 0)
        self.assertEqual(len(downstream.calls), 1)

    async def test_downstream_error_and_timeout_release_active_capacity(self):
        timeout_client = FakePortfolioClient(
            failure=DownstreamTimeoutError("portfolio request timed out")
        )
        timeout_app = _app_for(
            timeout_client,
            GatewayConfig(max_in_flight=1, queue_capacity=0, queue_timeout_seconds=1),
        )
        error_client = FakePortfolioClient(status_code=500, body={"detail": "failed"})
        error_app = _app_for(
            error_client,
            GatewayConfig(max_in_flight=1, queue_capacity=0, queue_timeout_seconds=1),
        )

        async with _client_for_app(timeout_app) as client:
            response = await _post(client)
        async with _client_for_app(error_app) as client:
            error_response = await _post(client)

        self.assertEqual(response.status_code, 504)
        self.assertEqual(error_response.status_code, 502)
        self.assertEqual((await timeout_app.state.admission.snapshot()).active, 0)
        self.assertEqual((await error_app.state.admission.snapshot()).active, 0)

    async def test_request_ids_generated_when_absent(self):
        downstream = FakePortfolioClient()

        async with _client_for(
            downstream,
            GatewayConfig(max_in_flight=1, queue_capacity=1, queue_timeout_seconds=1),
        ) as client:
            response = await _post(client)

        request_id = downstream.calls[0]["headers"]["X-Request-ID"]
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(request_id), 0)
        self.assertEqual(response.headers["X-Request-ID"], request_id)

    async def test_supplied_correlation_ids_forwarded_unchanged(self):
        downstream = FakePortfolioClient()
        headers = {
            "X-Run-ID": "run-1",
            "X-Request-ID": "request-1",
            "X-Query-ID": "query-1",
        }

        async with _client_for(
            downstream,
            GatewayConfig(max_in_flight=1, queue_capacity=1, queue_timeout_seconds=1),
        ) as client:
            response = await _post(client, headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(downstream.calls[0]["headers"], headers)
        self.assertEqual(response.headers["X-Run-ID"], "run-1")
        self.assertEqual(response.headers["X-Request-ID"], "request-1")
        self.assertEqual(response.headers["X-Query-ID"], "query-1")

    async def test_one_shared_downstream_async_client_lifecycle(self):
        created = []

        def handler(request):
            return httpx.Response(200, json=BUSINESS_RESPONSE)

        def factory(config):
            downstream = PortfolioApiClient(
                config,
                transport=httpx.MockTransport(handler),
            )
            created.append(downstream)
            return downstream

        app = create_app(
            config=GatewayConfig(max_in_flight=2, queue_capacity=2),
            client_factory=factory,
        )

        async with _client_for_app(app) as client:
            first = await _post(client)
            second = await _post(client)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0]._client.is_closed)

    async def test_no_automatic_whole_workflow_retry(self):
        downstream = FakePortfolioClient(status_code=503, body={"detail": "busy"})

        async with _client_for(
            downstream,
            GatewayConfig(max_in_flight=1, queue_capacity=1, queue_timeout_seconds=1),
        ) as client:
            response = await _post(client)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(downstream.calls), 1)

    async def test_high_connection_stress_bounds_active_queue_without_threads(self):
        downstream = FakePortfolioClient(delay_seconds=0.02)
        app = _app_for(
            downstream,
            GatewayConfig(max_in_flight=3, queue_capacity=5, queue_timeout_seconds=1),
        )
        before_threads = threading.active_count()

        async with _client_for_app(app) as client:
            responses = await _gather_posts(client, 40)

        statuses = [response.status_code for response in responses]
        self.assertLessEqual(downstream.max_active, 3)
        self.assertLessEqual(len(downstream.calls), 8)
        self.assertEqual(statuses.count(200), 8)
        self.assertEqual(statuses.count(503), 32)
        self.assertLessEqual(threading.active_count(), before_threads + 1)


def _app_for(downstream, config):
    return create_app(config=config, client_factory=lambda _config: downstream)


@asynccontextmanager
async def _client_for(downstream, config):
    async with _client_for_app(_app_for(downstream, config)) as client:
        yield client


@asynccontextmanager
async def _client_for_app(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


async def _post(client, *, ticker="AAPL", headers=None):
    return await client.post(
        "/v1/analyze",
        headers=headers,
        json={"holdings": {ticker: 1.0}, "lookback_days": 30},
    )


def _post_task(client, *, ticker="AAPL", headers=None):
    import asyncio

    return asyncio.create_task(_post(client, ticker=ticker, headers=headers))


async def _gather_posts(client, count):
    import asyncio

    return await asyncio.gather(
        *[_post_task(client, ticker=f"TICKER{i}") for i in range(count)]
    )


async def _wait_for_call_count(downstream, count):
    for _ in range(100):
        if len(downstream.calls) >= count:
            return
        await anyio_sleep(0.001)
    raise AssertionError(f"expected {count} downstream calls")


async def _wait_for_waiting(app, count):
    for _ in range(100):
        snapshot = await app.state.admission.snapshot()
        if snapshot.waiting >= count:
            return
        await anyio_sleep(0.001)
    raise AssertionError(f"expected {count} queued waiters")


async def _ignore_cancelled(task):
    try:
        await task
    except BaseException:
        pass


if __name__ == "__main__":
    unittest.main()
