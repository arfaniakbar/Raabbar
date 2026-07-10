    def handle_one_response(self):
        """
        Invoke the application to produce one response.

        This is called by :meth:`handle_one_request` after all the
        state for the request has been established. It is responsible
        for error handling.
        """
        self.time_start = time.time()
        self.status = None
        self.headers_sent = False

        self.result = None
        self.response_use_chunked = False
        self.connection_upgraded = False
        self.response_length = 0

        try:
            try:
                self.run_application()
            finally:
                try:
                    self.wsgi_input._discard()
                except _InvalidClientInput:
                    # This one is deliberately raised to the outer
                    # scope, because, with the incoming stream in some bad state,
                    # we can't be sure we can synchronize and properly parse the next
                    # request.
                    raise
                except socket.error:
                    # Don't let socket exceptions during discarding
                    # input override any exception that may have been
                    # raised by the application, such as our own _InvalidClientInput.
                    # In the general case, these aren't even worth logging (see the comment
                    # just below)
                    pass
        except _InvalidClientInput as ex:
            # DO log this one because:
            # - Some of the data may have been read and acted on by the
            #   application;
            # - The response may or may not have been sent;
            # - It's likely that the client is bad, or malicious, and
            #   users might wish to take steps to block the client.
            self._handle_client_error(ex)
            self.close_connection = True
            self._send_error_response_if_possible(400)
        except socket.error as ex:
            if ex.args and ex.args[0] in self.ignored_socket_errors:
                # See description of self.ignored_socket_errors.
                self.close_connection = True
            else:
                self.handle_error(*sys.exc_info())
        except: # pylint:disable=bare-except
            self.handle_error(*sys.exc_info())
        finally:
            self.time_finish = time.time()
            self.log_request()
