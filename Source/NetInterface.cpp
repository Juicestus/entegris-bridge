#include "NetInterface.h"

/* static */ NetErr NetInterface::InitNet()
{
#ifdef _WIN32
	WSADATA data;
	int err;
	if ((err = WSAStartup(MAKEWORD(2, 2), &data)))
	{
		FATAL("WSAStartup failed with %d", err);
		return NetErr::ERR_SOCKET;
	}
#endif
	return NetErr::OK;
}

/* static */ void NetInterface::CloseNet()
{
#ifdef _WIN32
	WSACleanup();
#endif
}


/* static */ NetErr NetInterface::ResolveHost(const char* host, ushort port, sockaddr_in_t* remote)
{
	if (!host || !*host) return NetErr::ERR_RESOLVE;

	// TODO: make portable

	memset(remote, 0, sizeof(*remote));
	remote->sin_family = AF_INET;
	remote->sin_port = htons(port);

	DWORD ip;
	if ((ip = inet_addr(host)) != INADDR_NONE)
		remote->sin_addr.S_un.S_addr = ip;
	else
	{
		struct hostent* he;
		if ((he = gethostbyname(host)) == NULL)
		{
			FATAL("Failed to resolve %s with %d", host, WSAGetLastError());
			return NetErr::ERR_RESOLVE;
		}
		memcpy(&remote->sin_addr, he->h_addr, he->h_length);
	}
	return NetErr::OK;
}



/**
 * 
 */
NetErr NetInterface::Open(struct sockaddr_in* remote, ushort timeout_ms)
{
	if (open) return NetErr::ALREADY_OPEN;

	/**
	 * Macros for convinently handling cleanup
	 */
#define RETURN_FAILED(FMT, ERRNO, ERR) do {	\
		FATAL(FMT, ERRNO);						\
			if (sock != INVALID_SOCKET)			\
			{									\
				closesocket(sock);				\
				sock = INVALID_SOCKET;			\
			}									\
			return ERR;							\
		} while (0);
	
	/**
	 * Create and setup socket 
	 */
	if ((sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP)) == INVALID_SOCKET)
	{
		RETURN_FAILED("Failed to create socket with %d", WSAGetLastError(), NetErr::ERR_SOCKET);
	}
	u_long mode = 1;
	if (ioctlsocket(sock, FIONBIO, &mode) == SOCKET_ERROR)
	{
		RETURN_FAILED("failed to set non-blocking with %d", WSAGetLastError(), NetErr::ERR_SOCKET);
	}
	
	/**
	 * Attempt to connect socket
	 */
	int err;
	if (connect(sock, (struct sockaddr*)remote, sizeof(*remote)) == SOCKET_ERROR)
	{
		if ((err = WSAGetLastError()) != WSAEWOULDBLOCK)
		{
			RETURN_FAILED("connect failed with %d", err, NetErr::ERR_CONNECT);
		}
		// still in progress, wait for writable
		fd_set wr, ex;
		FD_ZERO(&wr);	FD_SET(sock, &wr);
		FD_ZERO(&ex);	FD_SET(sock, &ex);

		struct timeval tv;
		tv.tv_sec = timeout_ms / 1000;
		tv.tv_usec = (timeout_ms % 1000) * 1000;

		if ((err = select(0, NULL, &wr, &ex, &tv)) == SOCKET_ERROR)
		{
			RETURN_FAILED("select failed with %d", WSAGetLastError(), NetErr::ERR_CONNECT);
		}
		if (err == 0)
		{
			RETURN_FAILED("connect timed out after %hu ms", timeout_ms, NetErr::ERR_TIMEOUT);
		}
		// refused / unreachable shows up in the exception set
		if (FD_ISSET(sock, &ex))
		{
			int so_err = 0, so_len = sizeof(so_err);
			getsockopt(sock, SOL_SOCKET, SO_ERROR, (char*)&so_err, &so_len);
			RETURN_FAILED("connect failed with %d", so_err, NetErr::ERR_CONNECT);
		}
	}
	
	/**
	 *  Set socket options 
	 */
	mode = 0;
	if (ioctlsocket(sock, FIONBIO, &mode) == SOCKET_ERROR)
	{
		RETURN_FAILED("failed to set blocking with %d", WSAGetLastError(), NetErr::ERR_SOCKET);
	}
	// SO_RCVTIMEO is a DWORD of ms here, a timeval on POSIX
	DWORD tmo = timeout_ms;
	if (setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, (char*)&tmo, sizeof(tmo)) == SOCKET_ERROR ||
		setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, (char*)&tmo, sizeof(tmo)) == SOCKET_ERROR)
	{
		RETURN_FAILED("failed to set timeouts with %d", WSAGetLastError(), NetErr::ERR_SOCKET);
	}

	open = true;
	return NetErr::OK;
#undef RETURN_FAILED
}


/**
 * 
 */
NetErr NetInterface::Send(byte* buf, size_t len)
{
	if (!open) return NetErr::NOT_OPEN;

	size_t sent = 0;
	int n;
	while (sent < len)
	{
		if ((n = send(sock, (char*)buf + sent, (int)(len - sent), 0)) == SOCKET_ERROR)
		{
			int err = WSAGetLastError();
			FATAL("send failed with %d", err);
			return err == WSAETIMEDOUT ? NetErr::ERR_TIMEOUT : NetErr::ERR_SEND;
		}
		sent += n;
	}
	return NetErr::OK;
}


/**
 * Read until the peer closes or we time out.
 * 
 * TODO: confirm with spec that exact read of header + size is ok termination 
 */
NetErr NetInterface::RecvAll(byte* buf, size_t cap, size_t* len)
{
	*len = 0;
	if (!open) return NetErr::NOT_OPEN;

	int n;
	while (*len < cap)
	{
		if ((n = recv(sock, (char*)buf + *len, (int)(cap - *len), 0)) == 0)
			return NetErr::OK;		// peer closed

		if (n == SOCKET_ERROR)
		{
			int err = WSAGetLastError();
			if (err == WSAETIMEDOUT)
			{
				// server icould be waiting on us to close
				if (*len) return NetErr::OK;
				FATAL("recv timed out with no data");
				return NetErr::ERR_TIMEOUT;
			}
			FATAL("recv failed with %d", err);
			return NetErr::ERR_RECV;
		}
		*len += n;
	}

	FATAL("response filled the buffer (%zu bytes), may be truncated", cap);
	return NetErr::ERR_RECV;
}


NetErr NetInterface::Close()
{
	if (!open) return NetErr::NOT_OPEN;

	// polite half close so the server sees EOF if it's waiting on us
	shutdown(sock, SD_SEND);
	closesocket(sock);
	sock = INVALID_SOCKET;
	open = false;
	return NetErr::OK;
}


NetInterface::~NetInterface()
{
	if (!open) return;
	Close();
}
