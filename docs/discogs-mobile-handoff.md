# Private phone handoff proposal

Status: design only. No VPN, device enrollment, public viewer, phone credential
or network policy has been configured for this proposal.

The existing noVNC viewer is bound to server loopback port 6080 and reached
through a Mac SSH tunnel. A phone's `127.0.0.1` refers to the phone, so the
current Mac URL is not a remotely usable phone link. A phone handoff should
control the same hosted browser and profile, rather than copy cookies or move
a verification image into another session.

## Proposed access boundary

Use Tailscale on the existing server and the owner's phone, with private
Tailscale Serve HTTPS forwarding to the existing `127.0.0.1:6080` listener.
Restrict access rules to the specifically approved phone and this viewer's
HTTPS port. Do not use the default broad device access policy. Keep public
Funnel, Tailscale SSH, subnet routes and exit-node functionality unused. Do not
expose raw VNC or noVNC on a public listener. This would let the owner use the
same Firefox display without keeping the Mac on.

This adds persistent device and network access to a signed-in browser. A person
controlling an approved device could operate that session. It therefore needs
specific approval before installing/enrolling the VPN, changing access policy
or starting Serve. Normal account sign-in or device approval must use supported
secure handoff; no authentication material belongs in chat or this repository.
Use a neutral, non-sensitive server name because HTTPS certificate names can
appear in certificate-transparency logs.

The published Personal allowance is currently zero dollars for six users and
unlimited user devices. Confirm the selected account and plan before setup;
this proposal authorizes no upgrade or new paid service.

## Runtime behavior

When human verification is detected, pause the pending read or transaction and
show the last safe classification and the private viewer handoff. The owner
would complete the ordinary verification in the same session. This is not an
automatic solver, a promise that challenges disappear permanently, or permission
to clear an explicit access denial. A later supported state check must still
revalidate identity, quote freshness, authorization and uncertain-order fences.
Do not retry an uncertain submission after a human handoff.

Mobile notification delivery is separate and unverified. A private viewer link
alone does not implement push notification delivery.

## Required acceptance checks

- Reach the same viewer from the approved phone over cellular with the Mac off.
- Verify WebSocket display updates and phone touch/keyboard controls.
- Reject viewer connections from unapproved devices and the public internet.
- Confirm no public 6080/VNC listener, unchanged profile and security stops.
- Verify device revocation and explicit stop handling without duplicate actions.

No such live checks have been performed. Exact Serve/noVNC WebSocket compatibility
in this deployment remains unverified.

## Primary sources reviewed

- [noVNC browser and mobile support](https://github.com/novnc/noVNC)
- [Private Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- [Tailscale access rules](https://tailscale.com/docs/features/access-control/acls)
- [Current pricing](https://tailscale.com/pricing)
- [HTTPS certificate-name disclosure](https://tailscale.com/docs/how-to/set-up-https-certificates)
