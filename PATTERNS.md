# Design patterns and SOLID principles

This document lists where each design pattern and SOLID principle is applied in the code: the class that implements it and the reason it was chosen.

## How the program is layered

```
 Browser (web/index.html)
     │  HTTP + JSON
 ForumWebApi ──► UserInteractionFactory ──► UserInteraction (commands)
     │                                          │
     ▼                                          ▼
 LoRaForumApp (facade, wires everything)   ForumService (rules) ──► ForumRepository (SQLite)
     │                                          │ events
     ▼                                          ▼
 PeerSynchronizer ──► RemoteForumProxy      ForumEventBus ──► ActivityFeed
     │                     │ names
     ▼                     ▼
 ForumLogPublisher ──► NdnNode (facade) ──► handler chain, ContentStore, PIT
                           │ frames
                       LoRaLinkFacade ──► SerialTransport (adapter) ──► USB ──► Pico ──► LoRa
```

## Patterns

| Pattern | Where (file → class) | Why we used it |
|---|---|---|
| **Proxy** | `forum/sync.py` → `RemoteForumProxy` (implements `ForumEventSource`; the real subject is `LocalForumEventSource`) | The other laptop's forum is far away and only reachable over a slow radio. The proxy acts as a local stand-in with the same interface: it turns method calls into NDN Interests, retries lost packets, caches answers, and tracks whether the peer is online. |
| **Facade** | `ndn/link.py` → `LoRaLinkFacade`; `ndn/node.py` → `NdnNode`; `app.py` → `LoRaForumApp` | Each one hides a complicated subsystem behind a few simple methods (`send()`, `fetch()`, `start()`). |
| **Factory Method** | `ndn/packets.py` → `PacketFactory`; `forum/events.py` → `ForumEventFactory`; `ndn/transport.py` → `TransportFactory` | They turn raw bytes into the correct packet or event class, and pick the right serial adapter for the computer. The callers never use `new` on concrete classes. |
| **UserInteractionFactory** (Factory Method + Command) | `forum/interactions.py` → `UserInteractionFactory`, `CreateTopicInteraction`, `AddCommentInteraction`, … | It generalises every user action. The web layer only says "create the `create_topic` interaction and run it". Adding a new action means adding one class. |
| **Command** | `forum/events.py` → `TopicCreated`, `CommentAdded`, `VoteCast`; `forum/interactions.py` → `UserInteraction` | Each change is an object with `apply()` / `execute()`. The same event object is stored, sent over LoRa, and replayed on the other laptop. |
| **Chain of Responsibility** | `ndn/handlers.py` → `DuplicateInterestFilter → ContentStoreResponder → ProducerResponder → PendingInterestSatisfier → UnhandledPacketLogger` | Each incoming packet is passed along the chain until one handler deals with it. New rules are new links in the chain. |
| **Template Method** | `ndn/handlers.py` → `PacketHandler.handle()`; `forum/interactions.py` → `UserInteraction.run()` | The base class fixes the steps, and subclasses fill in one step. |
| **Observer** | `forum/observers.py` → `ForumEventBus`, `ActivityFeed`, `RadioTrafficLog`; `NdnNode.subscribe()` | The web page's activity feed and live packet log get notified about changes without the service or the network knowing about the web page. |
| **Strategy** | `forum/ranking.py` → `NewestFirst`, `Trending`, `TopVoted`, `MostDiscussed`; `ndn/node.py` → `Producer` | They let you swap how topics are ordered, or how Data is produced, at runtime. |
| **Adapter** | `ndn/transport.py` → `PyserialTransport`, `PosixTtyTransport`, `MemoryTransport` | They make three different "serial" APIs look the same, so the real Pico and the simulator can be swapped. |
| **Repository** | `forum/repository.py` → `TopicRepository`, `CommentRepository`, … and the `Sqlite…` classes | They keep all SQL in one place. The rules (`ForumService`) never see SQL. |

## SOLID

| Principle | Example |
|---|---|
| **S** Single Responsibility | `LoRaLinkFacade` only moves bytes, `PacketFactory` only builds and parses packets, `ForumService` only holds forum rules, and `ForumWebApi` only handles HTTP. |
| **O** Open/Closed | A new packet handler, event type, user action or sort order is a new class plus one registration line. Existing classes don't change. |
| **L** Liskov Substitution | Any `SerialTransport` (real Pico or simulator) or any `ForumEventSource` (local log or remote proxy) can replace another. The tests rely on this. |
| **I** Interface Segregation | Small repository interfaces (`TopicRepository`, `VoteRepository`, …) instead of one big database class. |
| **D** Dependency Inversion | `ForumService` depends on repository *interfaces*. `NdnNode` gets its link from outside. Only `app.py` (the composition root) creates concrete classes. |

## NDN in three sentences (if asked)

Instead of sending data *to an address*, a node asks for data *by name* with an **Interest** packet, for example `/forum/qanoo/log/7`, and whoever has that data answers with a **Data** packet of the same name. Every node keeps a **Content Store** (cache), a **Pending Interest Table** (what it is waiting for), and a list of name prefixes it can produce. Our forum is just named data: each laptop publishes its own event log under `/forum/<name>/…`, and the other laptop fetches it by name over LoRa.
