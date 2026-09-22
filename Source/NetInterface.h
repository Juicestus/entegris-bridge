#pragma once

#include "Util.h"

using sockaddr_in_t = struct sockaddr_in;

enum class NetErr : int 
{
	OK = 0,
	ERR_RESOLVE,	// bad IP / no DNS entry
	ERR_SOCKET,
	ERR_CONNECT,
	ERR_SEND,
	ERR_RECV,
	ERR_TIMEOUT,
	NOT_OPEN,
	ALREADY_OPEN,
};

class NetInterface
{
	SOCKET sock{ INVALID_SOCKET };
	bool open{ false };

public:
	static NetErr InitNet();
	static void CloseNet();

	static NetErr ResolveHost(const char* host, ushort port, sockaddr_in_t* remote);

	NetInterface() {}

	/* 
	 * NOTE: SO_RCVTIMEO is a DWORD of ms on windows but a timeval on POSIX
	 * and blocking connect() ignores it 
	 */
	NetErr Open(sockaddr_in_t* remote, ushort timeout_ms);
	
	NetErr Send(byte* buf, size_t len);

	/* until peer closes or timeout, TODO: exact read once size field is confirmed */
	NetErr RecvAll(byte* buf, size_t cap, size_t* len);

	NetErr Close();

	~NetInterface();
};
